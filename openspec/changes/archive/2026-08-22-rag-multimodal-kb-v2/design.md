## Context

本变更基于 proposal.md（Why/What/Capabilities 参照其 §10.2 V2 路线），作用域为 monorepo 中 `services/rag-service` 子系统（端口 8002，六层架构：api → models → parsers → repositories → services → vectorstore）。已归档 v1 主 spec 为：

- `openspec/specs/rag-service/document-ingest/spec.md`（6 条 Req）
- `openspec/specs/rag-service/hybrid-search/spec.md`（7 条 Req）
- `openspec/specs/rag-service/search-logging/spec.md`（3 条 Req）

**兼容硬约束（来自 proposal §10.3）**：

1. DB：只加列/加表，不改既有列语义；status 枚举 Alembic 增量扩展；kb_search_logs BOOLEAN→VARCHAR 提供默认值，不强制回填。
2. 接口：对外契约（路径/响应包/错误码/分页）完全向后兼容；新字段可选、新端点不修改旧端点入参。
3. 索引：Milvus/ES 只增字段不删字段；发布窗口一次性重建；重建前校验 status 与索引一致。
4. 缓存：Key 引入 v2 段（`rag:cache:query:v2:*`），V1 Key 自然过期不迁移（避免双写迁移代码）。
5. 行为开关：4 个检索增强开关（Rewrite/Routing/Weighting/RowLevelAccess）+ 1 个降级入库开关（EMBED_DEGRADED_KEEP_TEXT）+ 1 个多轮开关（MULTITURN），默认全 false = V1 行为。
6. 技术栈继续对齐 step4/02 选型（DashScope LLM 复用账号，不新增 LLM 供应商；PG15/Milvus2.4/ES8.11/Redis7/embed-service/rerank-service 全部沿用 V1）。

## Goals / Non-Goals

**Goals（设计目标）**：

- 完成 5 项新能力、3 项现有能力修改的技术落地路径设计（见 proposal Capabilities），每项给出可落地的模块/目录规划。
- 明确 3 条 Alembic 迁移（0004/0005/0006）的变更内容、回填策略、风险点。
- 明确 Milvus `kb_vectors` 重建、ES `kb_bm25→kb_chunks_bm25` 双写过渡、Redis 冒号化三项存储整改的执行步骤。
- 明确新外部依赖部署边界：`deploy/ocr/` PaddleOCR 容器、ASR Provider/DashScope LLM 调用参数、Redis Pub-Sub channel 订阅启动钩子。
- 提供**开关回退方案**：发布后 48h 内若出现问题，一键关 7 个开关恢复 V1 行为（无状态副作用，无需 DB rollback）。

**Non-Goals（设计非目标）**：

- 不实现 LangSmith 追踪、评估模块、版本灰度（V3 规划）。
- 不实现敏感词 CRUD（仅提供 DB 表 + seed + 版本号 + Pub-Sub，CRUD 归 V3-T5）。
- 不主动调用 case-service / data-platform（rag-service 边界：被动服务）。
- 不强制回填 V1 文档的 customer_id/agent_id/call_date/risk_tag 元数据（由 data-platform 推送增量回填，缺省 null 默认等权）。
- 不修改 kb_documents.status 的既有语义（processing/active/archived/failed 不变；不在本次引入 version status/灰度 status 等新值）。

## Decisions

### Decision 1：OCR 独立容器化 sidecar（PaddleOCR CPU + HTTP /ocr）

- **Rationale**：PaddleOCR 依赖重（paddlepaddle + opencv + 3 模型权重 ~1GB），不能放入 rag-service 主镜像（主镜像体积、启动时间、冷启动内存峰值均不可控）。独立 sidecar 允许：① 容器独立扩缩容；② OCR 崩溃不拖垮 rag-service；③ 后续可替换为 GPU 版无代码变更。
- **实现方式**：新增 `deploy/ocr/` 目录，Dockerfile 基于 paddlepaddle:2.6.0-runtime，FastAPI 暴露 `POST /ocr`，入参 `file (bytes) + lang="ch"（默认）`，出参 `{text: str, layout: [{bbox, text, score}]}`，超时 30s。rag-service 内新增 `app/services/ocr_client.py`（HTTPClient + timeout + retry + 3-fail-circuit-breaker，沿用 EmbedClient 模式）。
- **替代方案**：Python 库直接引入 PaddleOCR → 否决：主镜像膨胀 1.2GB，gunicorn worker OOM 风险高，k8s HPA 无法单独扩 OCR。

### Decision 2：ASR Provider 抽象（DashScope Paraformer 默认，Whisper 接口预留）

- **Rationale**：PRD 规定不新增 LLM 供应商。DashScope 已在 call-service 使用，统一计费/Key 管理。但考虑未来离线私有部署场景，引入 Provider ABC：`ASRProvider.transcribe(bytes, fmt) -> str`，默认 `DashScopeASRProvider`（走 `/api/v1/services/aigc/speech/asr` Paraformer 模型），`WhisperASRProvider` 作为 fallback 仅实现接口签名（不接实现）。
- **Rag-service 接入点**：`app/services/asr_client.py`（Factory.create("dashscope") 读取环境 ASR_PROVIDER，超时 60s + 3 次重试 + 熔断）；ParserFactory 新增 `audio_parser.py` → 读文件 → 调 asr_client → 返回 str，后续链路复用 ChunkService。
- **替代方案**：内部 Whisper.cpp 本地推理 → 否决：CPU 速度 0.3x RT，90s 录音需要 270s，无法满足 60s 分片+组合 < 120s 的入库 SLA。

### Decision 3：Query 改写使用 DashScope Chat 接口 + 严格输出 JSON 契约

- **Rationale**：QueryRewrite 要求 `rewritten_query + confidence`，用 DashScope `/api/v1/services/aigc/text-generation/generation` 配合严格 Prompt（要求输出 JSON `{"rewritten_query": str, "confidence": float}`，并加正则校验 fallback）。失败/非法 JSON → confidence=0 触发降级原 query（spec 中<0.6 已覆盖）。
- **开关接入点**：`SearchService.search()` 第一阶段：if KB_QUERY_REWRITE_ENABLED → RewriteService.rewrite(query) → (use_query, rewritten_query_tag, degraded_flag)。
- **替代方案**：Prompt 动态配置（V3-T4）→ 延后：V2 先硬编码 V1 专用 Prompt，不引入 prompt_spec 动态加载机制。

### Decision 4：路由规则版使用 YAML 配置 + 启动加载 + SIGHUP 热重，无模型推理

- **Rationale**：V2 先规则后模型（PRD §10.2 技术取舍 意图分类先规则）。配置 `conf/intent_routes.yaml`：`intent_tag → {keywords: [], weights: {category: float}}`。启动时 LoadFile，**注册 SIGHUP handler 热重（无需重启进程）**。空配置/文件缺失 → bypass degraded=true。
  - **⚠ 实现检查点（对齐 tasks §8.5，Apply 代码修复前未达）**：main.py startup 末尾必须存在 `signal.signal(signal.SIGHUP, _handler)`；handler 获取全局 RoutingService 实例后执行 `.reload(path)` 并打印 info log。发布前静态门禁：`grep -q 'signal.signal.*SIGHUP' services/rag-service/app/main.py` 退出码为 0，否则构建失败。发信号到子进程的单测（tasks 8.6）依赖此实现才能通过。
- **权重施加位置**：RRF 融合前的两路 rank → 修改 rank（不修改 score，保持 RRF k=60 公式不变）→ `adjusted_rank = rank / weight`（权重大→排名靠前→RRF 分子同，分母小→得分高）。保证加权不影响融合算法本身可验证性。
- **替代方案**：分类召回时分 query filter 只召某些类 → 否决：会出现"规则命中但该类无资料"，降级链复杂；加权方式容错好（只调排名）。

### Decision 5：业务加权的权重公式 & 配置来源

- **公式**：weight = 1.0；if `within_7_days(call_date)` then ×1.5；if `risk_tag=="high"` then ×1.3。叠加无上限。其余字段 customer_id/agent_id V2 保留但不参与权重（V3 可能按 agent 历史命中数调整，本次留作行级权限用）。施加在 RRF score × weight 后，送入 Rerank。
- **元数据来源**：`kb_documents` 表 Alembic 0004 增 4 列（customer_id UUID NULL / agent_id UUID NULL / call_date TIMESTAMP NULL / risk_tag VARCHAR(32) CHECK IN ('high','medium','low',NULL)）。V1 存量文档全 NULL。
- **读取方式**：SearchService 在融合阶段批量 `DocRepo.get_meta_dict(doc_ids)`，单次 SELECT IN 不引入 N+1。
- **替代方案**：写 Milvus 冗余字段 → 否决：Milvus 需重建索引 + 维度冗余维护；PG JOIN 在 10k doc_ids 级仍 <5ms，可接受。

### Decision 6：行级权限采用 YAML 角色矩阵映射 + JWT role/dept claim 读取

- **配置**：`conf/row_access_matrix.yaml` 格式：`(role, dept) → [categories]`；`(default, default) → [all 4 categories]` 保底。未知组合走 default（避免全过滤空）。
- **JWT Claim 来源**：复用 `require_role.py` 中 JWT payload 解析，取 `role`（str，upper 归一）和 `department`（str）claim 字段；两者缺省均为 "default"（department 为 "UNKNOWN" 或缺省时归一到 "default"）。**department claim 由认证/签发系统（如 SSO、JWT issuer）在登录时写入 payload，本 V2 版本不修改 users 关系表（step4/01 DDL 无 department 列）**。若认证系统暂未签发 department，走 default→全 4 类放行矩阵分支（V1 兼容等价）。
- **过滤施加位置**：Rerank 之后、截取 top_k 之前（避免 V1 排序受白名单影响）。
- **Tenant_id 预留**：JWT 加可选 `tenant_id` claim + 配置中 `(tenant_id, role, dept)` 三元组可选；V2 若 JWT 未携带 tenant_id 或矩阵未声明三元组键，则 tenant_id 分支**整体 bypass**（不影响现有单组织部署）。
- **替代方案**：PG kb_documents RLS（行级安全策略） → 否决：V1 服务用共享 DB user 连接，RLS 需 per-request SET LOCAL role，改造量大且 SQLAlchemy session 适配复杂；应用层过滤更可控。

### Decision 7：多轮会话 Redis v2 Key + 最近 10 轮 LIST cap

- **数据结构**：`rag:session:v2:{session_id}` 为 LIST；每轮 `LPUSH json.dumps({query, rewritten_query, top_doc_ids, ts})`，然后 `LTRIM 0 9`；`EXPIRE 1800`。全部操作 1 个 pipeline 原子。
- **Session ID 生成**：缺失时用 `ulid.new()` 生成；响应头 `X-Session-Id` 返回。ULID 大小写可忽略。
- **上下文补全接入点**：Query 改写 Prompt 中拼接最近 3 轮 `[{query}→命中 {title 摘要}]`，作为"Context" 附加段送入 LLM。若 Rewrite 关闭，则使用规则式拼接：`full_query = history_q3 + " " + history_q2 + " " + history_q1 + " " + current_query`。

### Decision 8：敏感词 V1 文件→V2 DB 迁移分三步（保障切换零停机）

1. **Step A**：Alembic 0004 创建 `sensitive_words` 表（若不存在）并 seed 50 词 baseline（category="threat" severity="medium" status="active" 每条 from V1 data/sensitive_words.dict 逐行同步，使用 INSERT ON CONFLICT DO NOTHING）。
2. **Step B**：`SensitiveWordsService` 实现双读：启动先检查 DB status=active 行数量是否 ≥ 50；是则**读 DB**，否则**回退读 .dict file**（兼容 DB 尚未初始化的场景）。对外接口 `/sensitive-words/current.dict` text/plain 行为不变。
   - **Step B+（启动自校验幂等，对齐 tasks §11.6，Apply 代码修复前未达）**：main.py startup 时在 DB/Redis 初始化完成后执行一次"启动自检"：
     1. `SELECT COUNT(*) FROM sensitive_words WHERE status='active' and is_delete='0'` 取 count；
     2. 若 count < 50 且 `data/sensitive_words.dict` 可读 → 对 .dict 每行再次执行 `INSERT INTO sensitive_words(...) VALUES (...) ON CONFLICT (word) DO NOTHING`（保障极端 deploy 顺序：先启动容器后才跑 Alembic 时，仍能在后续新容器启动时补上 baseline，无需人工干预）；
     3. 初始化 Redis key `rag:sensitive_words:version`：若不存在则 SET 为 1（非 TTL，作为 INCR 起点）；
     4. 最终若 count < 50 仍失败 → logger.warning 明确告警，但不抛异常（服务仍可启动，运行期 get_current_dict fallback 到 .dict 文件），保障不阻塞启动同时运维可见。
3. **Step C**：Refresh 事件。内部 INTERNAL 接口 `POST /sensitive-words/refresh`（ADMIN 权限，由管理后台触发）执行：① DB 读；② Redis INCR `rag:sensitive_words:version`；③ Redis PUBLISH `kb.sensitive_updated` version_str。qc-service 按其订阅侧方案 60s 热刷新。

### Decision 9：存储整改三部分的技术落地

**a) Milvus kb_vectors 增 version(INT64) + source(VARCHAR255)**：发布窗口内执行 `scripts/rag/rebuild_milvus_v2.py`：① 查询所有 kb_chunks 有 vector_id 的记录；② 重新 embed；③ 在新 collection `kb_vectors_v2`（带 version/source 字段 + IVF_FLAT 参数同旧）批量 insert；④ 校验一致（count 比对 + sample 1% 余弦相似）；⑤ Milvus collection 名探测自动切换（无 alias API 操作，运行期 has_collection 探测集合名隐式切换）；⑥ 失败：collection 名探测切回旧 collection + 删 v2。窗口内接受短时不可用。V1 文档 version=1，source="v1_legacy"。OCR/ASR 文档 source="ocr"/"asr"，version=1。

**b) ES kb_bm25 → kb_chunks_bm25 双写过渡**：① 新建 index `kb_chunks_bm25` 同 mapping（同规范 step4/02 七的 version/source/customer_id/agent_id/risk_tag/created_at 新增字段补全缺省；不含 expired 字段，与 es_v2_mapping.json / es_client.py 双写一致）；② `EsClient` V2 双写（写入 kb_bm25 + kb_chunks_bm25），只读 kb_bm25（V1 模式）→ ③ 运维跑 `reindex_es_v2.py` 旧 index 灌到新 → ④ 配置切只读新 index → ⑤ 配置切只写新 index → ⑥ 30 天后删旧 index。全程零停机。旧 index 中无 version/source 字段返回缺省。

**c) Redis key 冒号化 + 兼容别名**：`rag:embed:degraded`（V1 已用冒号，实际 V1 代码是 `rag:embed:degraded`？PRD §4.4 清单中 key 是 `rag:embed:degraded` 用冒号，但 A10 说"熔断标识用下划线"——需要核实。按 PRD A10 原文：`rag:embed:degraded`/`rag:rerank:degraded` 用下划线违反 → 代码要改为冒号命名？PRD A10 说 "熔断标识用下划线"。但 PRD §4.4 列出的 V1 已用 key：`rag:embed:degraded`、`rag:rerank:degraded` 已经是冒号（段之间是冒号），只是 degraded 前面的最后一段之前的是冒号。所以 A10 说的是"熔断标识 rag:embed:degraded 使用冒号分隔（与 {service}:llm_degraded step6/01 风格一致），但 step4/03 全局 key 规范 <prefix>:<entity>:<id> 格式要求冒号（两者冲突）"。结论：保持现有 `rag:embed:degraded` 不变（step6/01 风格与 V1 已经落地），在 step4/03 收录清单中增加此 5 个已落地 key 的记录说明命名冲突已协调。不做代码级改 key（风险 > 收益，且无调用方影响）。——**本决定不做 key 改名，仅做规范清单补充收录。**

### Decision 10：kb_search_logs BOOLEAN → VARCHAR('0','1') 迁移 + 双写过渡期

- **Alembic 0005**：
  1. `ALTER TABLE kb_search_logs ADD COLUMN degraded_tmp VARCHAR(1) CHECK (degraded_tmp IN ('0','1')) DEFAULT '0' NOT NULL;`，is_fallback 同理 degraded_tmp→is_fallback_tmp。
  2. `UPDATE kb_search_logs SET degraded_tmp = CASE WHEN degraded THEN '1' ELSE '0' END;`（V1 全量回填，单表 10w 行级别秒级完成）。同理 is_fallback。
  3. **发布窗口 7 天内禁止 DROP 列 / 禁止 RENAME 列**。V2 应用层 INSERT / UPDATE 写 VARCHAR tmp；读判断用 `degraded_tmp == '1'`。旧 BOOLEAN 列 degraded / is_fallback 作为 V1 rollback 时的写入目标完整保留，发布窗口内不删除。
  4. **Alembic 0006_post_cleanup（发布 30 天观察期通过后执行，对齐 README Runbook Step 6）**：① ALTER DROP degraded BOOLEAN 旧列；② ALTER DROP is_fallback BOOLEAN 旧列；③ ALTER TABLE RENAME COLUMN degraded_tmp → degraded；④ ALTER TABLE RENAME COLUMN is_fallback_tmp → is_fallback。此迁移 **不与 0005 一起执行**，保证 V1 回滚安全。
- **应用层改造**：`LogService.save()` 写 VARCHAR '0'/'1'；读判断时用 `== '1'`。cache_hit 字段 VARCHAR '0'/'1'。查询日志界面如有也改为 VARCHAR 字符串判断。整个过程无数据丢失，可回滚（若 0006 尚未执行，Alembic downgrade 0005 时反向删除 tmp+5 新列，BOOLEAN 旧列仍在，恢复写入 BOOLEAN；若 0006 已执行则 0006 downgrade 反向 ADD BOOLEAN + 反 CASE WHEN 从 VARCHAR 填回）。
  - **⚠ 三方一致约束（tasks §3.7-§3.12、本 Decision 10 第 3/4 条、README Runbook Step 6）**：Alembic 0005 upgrade **不能包含任何 DROP BOOLEAN 列 / RENAME tmp 列语句**。发布前静态门禁：`grep -E 'DROP COLUMN.*(degraded|is_fallback)[^_]|RENAME.*(degraded_tmp|is_fallback_tmp)' migrations/versions/0005_*.py` 必须退出码非 0（即不能命中）；仅 0006_post_cleanup.py 才能命中。

## Risks / Trade-offs

| Risk                                                                                                                                                                                                                                                                                                                                                          | Mitigation                                                                                                                                                                                                                                                                                                                                                                                |
| :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PaddleOCR CPU 慢（1 张制度截图 ~3s）** → 批量 OCR 任务堆积，上传接口 >30s 超时                                                                                                                                                                                                                                                                              | ① OCR 接口独立容器 + k8s HPA（QPS≥5 自动扩容到 4 pod）；② 上传文件如果是图片+音频时，解析超时 30s 内必须完成，否则 A3001 失败，不排队阻塞；③ 后续考虑 GPU PaddleOCR 替换，Provider 抽象可兼容。                                                                                                                                                                                           |
| **DashScope LLM 改写不确定性** → 低置信度改写送入后产生幻觉                                                                                                                                                                                                                                                                                                   | ① confidence<0.6 强制降级原 query（spec 强制 + 单测覆盖 V2-T4 置信度场景）；② 改写结果写入 kb_search_logs，便于 BadCase 分析；③ DashScope 调用超时 10s+熔断，失败 bypass 原 query。                                                                                                                                                                                                       |
| **Milvus 重建窗口短时不可用** → 检索全失败                                                                                                                                                                                                                                                                                                                    | ① 选择凌晨发布窗口（3:00-3:30 AM）；② alias 原子切换脚本附带 rollback_alias_on_failure.sh（脚本检测 collection.count 对齐后才切）；③ 切换期间 ES-only 降级（Milvus 不可用时走 ES-only，degraded=true，V1 降级矩阵已覆盖，用户感知=结果差但不出错）。                                                                                                                                      |
| **kb_search_logs VARCHAR 迁移回填期间插入新行** → 丢失 degraded/is_fallback 值                                                                                                                                                                                                                                                                                | ① 先 ADD COLUMN ... DEFAULT '0'，再 UPDATE 回填，应用层在 INSERT 时同时写 degraded_tmp = '1'/'0'（V2 应用在 migration 跑之后启动）；② 顺序：migrate → deploy app → verify。                                                                                                                                                                                                               |
| **敏感词 V1 50 词与 V2 seed DB 不一致** → qc-service 端 AC 自动机重建后敏感词匹配失配                                                                                                                                                                                                                                                                         | ① Seed 迁移使用 `for line in data/sensitive_words.dict` 逐行 INSERT ON CONFLICT DO NOTHING；② 单元测试 `test_sensitive_words_seed_consistency.py`：断言 DB status=active count=50 且与 dict 文件内容完全一致（顺序无关集合对比）。                                                                                                                                                        |
| **ES 双写不一致**（双写期间两个 index 数据差）                                                                                                                                                                                                                                                                                                                | ① 双写阶段读旧 index，保证写入新 index 通过校验脚本每 5min 对比 count；② reindex_es_v2.py 完成后切换读；③ 30 天观察期内保留旧 index 作为快照。                                                                                                                                                                                                                                            |
| **ES-only 降级时闸门 1 不可用 → 空语义兜底只有闸门 2**。对齐「hybrid-search spec 闸门 1」定义：闸门 1 = Milvus dense cosine 相似度最高值 < 0.6，仅在 dense_hits 非空时生效；Milvus 宕机/ES-only 场景下 dense_hits 为空时闸门 1 短路跳过，空语义仅靠闸门 2（rerank_score < 0.35）兜底。Runbook Step 5 灰度时需保障开启 Weighting=true 不放大 BM25 的偏序噪声。 | ① **ES v2 mapping 配置 ik_max_word 分词器已生效**（es_v2_mapping.json analyzer）；② Step 5 灰度时先开 Weighting 再开其他开关，观察 ES-only 场景下 kb_search_logs 中 is_fallback='1' 比例保持 < 5%（阈值告警同 step3/03 空检索命中监控）；③ 若比例突破则回退闸门 1 改为取融合分阈值（=选项 A，apply 代码修复时新增常量 RAG_FUSED_SCORE_THRESHOLD≈0.005），保障 ES-only 下闸门 1 也能工作。 |
| **Alembic 0005 迁移 DROP BOOLEAN 列过早 → V1 rollback P0 崩溃**（设计 Decision 10 要求的 7 天双写过渡期如果未执行到位）。发布前必须保障 0004/0005 upgrade 后 BOOLEAN 列仍存在，不能仅靠肉眼审查。                                                                                                                                                             | 发布前静态门禁脚本：`grep -E 'DROP COLUMN.\*(degraded                                                                                                                                                                                                                                                                                                                                     | is*fallback)[^*] | RENAME.\*(degraded_tmp | is*fallback_tmp)' migrations/versions/0005*\*.py` → **必须退出码非 0（即 0005.py 里不能出现 DROP/RENAME BOOLEAN 列语句）**。若需要执行真正的 DROP+RENAME，只能在 0006_post_cleanup.py（发布 30 天后单独迁移）中。对应该门禁写入 CI gate。 |

## Migration Plan

部署 & 回滚计划（共 6 步，发布日生产）：

1. **Pre-deploy（前一天夜间）**：
   - 部署 `deploy/ocr/` PaddleOCR 容器（3 副本），健康检查就绪（`GET /health`）。
   - 跑 Alembic 0004（kb_documents 元数据列 + sensitive_words 表 seed）、0005（kb_search_logs 新列 + 回填）。检查 row 数：sensitive_words count=50（与 .dict 对齐），kb_search_logs degraded_tmp 全量 NOT NULL。
   - ES：创建新 index `kb_chunks_bm25`（带新增字段），开启双写开关 `ES_DUAL_WRITE=true`（仅 V2 app 代码内）。跑 reindex_es_v2.py 同步 kb_bm25→kb_chunks_bm25。
   - Milvus：创建 collection `kb_vectors_v2`（带 version/source），**暂不切换 collection 名探测目标**。

2. **Deploy V2 app**：
   - 滚动发布 rag-service:v2 容器。7 个功能开关默认全 false（=V1 行为）。
   - 全量跑 V1 77 用例回归 → 必须 100% 通过（开关全关行为一致）。
   - 跑 smoke_rag_v2.sh（含 OCR PNG 上传 + ASR MP3 上传 + QueryRewrite + Routing + Weighting + RowLevel + Session 灰度 5% 流量）。

3. **Milvus 切换（发布窗口 3:00-3:30）**：
   - 跑 rebuild_milvus_v2.py：旧 collection → v2（带 version=1, source="v1_legacy"）。校验 count 对齐（旧 collection.count == new collection.count）。
   - 执行 Milvus collection 名探测自动切换（无 alias API 操作）。
   - 健康检查：`GET /health` 通过（Embedding+Milvus 探活正常）；跑 20 条检索回归，命中溯源 version=1。
   - **回滚**：collection 名探测切回旧 collection。V2 应用能读旧 collection schema（缺省 version/source=null）。

4. **ES 切换（紧随 Milvus 切换后）**：
   - 运维切 ES_READ_INDEX=kb_chunks_bm25。
   - 验证 3.5 中双写一致（count 对比）。
   - **回滚**：切 ES_READ_INDEX=kb_bm25。

5. **灰度开启新能力（发布后 2 天，监控稳定）**：
   - Day 2 AM：开 `KB_QUERY_REWRITE_ENABLED=true`（低风险，confidence 兜底）。
   - Day 2 PM：开 `KB_ROUTING_ENABLED=true` + 传标准 YAML。
   - Day 3 AM：开 `KB_WEIGHTING_ENABLED=true`（缺省 1.0 行为）。
   - Day 3 PM：开 `KB_ROW_LEVEL_ENABLED=true` + 传矩阵 YAML（默认兼容全放行）。
   - Day 4 AM：开 `KB_MULTITURN_ENABLED=true`。
   - 每一步开关开启后观察 2h：step6/02 监控项（检索命中率<60%告警/Embedding队列积压>100），出现问题立刻关开关。

6. **Post-cleanup（发布 30 天后，观察无异常）**：
   - DROP Milvus 旧 collection。
   - ES 删除 kb_bm25 旧 index。
   - Alembic 0006（可选）：删除 kb_search_logs 旧 BOOLEAN 列，重命名 tmp 列。
   - Redis V1 cache keys 已全部自然过期（TTL=1h），无需清理。

## Open Questions

> 可在 coding 阶段或后续 v2.x 小版本解决，不影响当前 proposal / specs / tasks 设计。若业务确需再确认。

1. **ASR 说话人分离**：PRD 提及"按质检规则过滤空段和静音"，但未要求"按说话人分角色（坐席/客户）"分别建 chunk。V2 首版暂不拆分。若 qc-service 后续需要可在 ASR Provider 返回结构增加 speaker_id 字段引入。
2. **行级权限 tenant_id 启用时机与 department claim 来源**：已决策 department 由 JWT 签发系统写入 claim（本版本不修改 users 表），tenant_id 预留 bypass 生效；若后续需多租户部署再新增矩阵三元组声明即可，不再作为 Open Question。
3. **业务加权 1.5/1.3 系数可配置化**：当前硬编码在 WeightingService，若业务需调整可放到 `conf/weight_rules.yaml`。此重构可在 v2.1 迭代执行。
4. **DashScope 费用预算管控**：QueryRewrite 按检索次数计费。若日活 1k 坐席 × 每小时 10 次 × 8h = 8w 次/天，需运维侧确认 DashScope 额度预充值足够。属运维问题，不改代码。

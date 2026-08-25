# RAG 多模态知识库 V3 封版变更摘要

> 文档属性：封版校验配套文档（只读，不参与代码变更）
> 变更：rag-multimodal-v3（已归档 → `openspec/changes/archive/2026-08-24-rag-multimodal-v3/`）
> 生成日期：2026-08-24
> 处理原则：增量修复，不改动 V1/V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10 保持原样）
> 处置口径：① 代码修复（Apply 落盘）② Spec 对齐（/opsx-sync 合并主 spec）③ 遗留项移交（V4 迭代 / 后续治理变更）

---

## 一、V3 变更总览

**背景**：V2 封版校验与归档评审遗留 19 项非阻塞缺陷（偏差说明 B2/M1/L1-L15 + 归档 tasks §19 风险登记 19.1-19.6）。V3 以"增量修复、不改动成熟功能与对外契约"为原则集中闭环。

| 维度 | 内容 |
| ---- | ---- |
| 变更类型 | 全部为既有能力修复，无新增能力（New Capabilities = 0） |
| 涉及 capability | 8 个（全部 MODIFIED） |
| 代码改动 | `services/rag-service` 10+ 文件（增量分支 / 开关接入，不改既有逻辑） |
| 配置 | 新增 2 开关（默认 false）+ `faster-whisper` 依赖（全局 requirements） |
| 验证 | 单元测试 181 passed / 1 skipped；`openspec validate --specs` 8/8 通过；冒烟主链路 PASS |

**Non-Goals（本次明确不做）**：LangSmith 追踪 / 评估模块 / 文档版本管理+灰度 / Prompt 动态配置 / 敏感词 CRUD（DD-03 §11.4 V3-T1~T5，治理能力另行排期）；LLM 摘要生成（19.5，无实施对象，仅作设计约束记录）。

---

## 二、代码修复清单（Apply 落盘）

### 2.1 安全 / 稳定性修复（V2 风险 19.2 / 19.3）

| 条目 | 位置 | 修复内容 |
| ---- | ---- | ---- |
| 19.2 Redis 故障连锁 500 | `search_service.py` L288 | `rerank.is_degraded()` 熔断探测包 `try/except`，异常按"未熔断"处理 + warning 日志 |
| 19.2 Redis 故障连锁 500 | `search_service.py` L392-393 | `session_svc.record()` 包 `try/except`，失败仅 warning 不阻断检索返回 |
| 19.3 权限过滤 fail-open 越权 | `search_service.py` L321-322 + `row_access_service.py` | 改为 fail-closed 三段式：error 日志（含 trace_id）→ `get_default_matrix()` 保底矩阵重放 → 保底仍失败置 `reranked=[]` 走下游兜底闸门 |

### 2.2 功能补齐（V2 偏差 B2 / M1 / L5）

| 条目 | 位置 | 修复内容 |
| ---- | ---- | ---- |
| B2 seed 初始化未 PUBLISH | `main.py` L100-139 + `sensitive_words_sync.py` | 新增 `publish_version()` 方法（抽取 refresh 既有 PUBLISH 逻辑），seed 自检 INSERT 成功后发布 `kb.sensitive_updated`（payload `{"version","trace_id","ts"}`）；已 50 词跳过（幂等） |
| M1 ASR VAD 分片桩实现 | `asr_client.py` L69-84 | `_trim_silence` 真实实现（WAV-PCM 按 RMS<0.01 裁剪首尾静音，窗口 0.1s）；`_split_segments` ≤60s 硬分片；`transcribe` 逐片转写按时间序拼接；非 WAV 透传 |
| L5 Whisper Provider 骨架 | `whisper_provider.py` L11-17 | 骨架实现为可用 WhisperProvider（本地模型转写，返回与 DashScope 一致的 `{text}` 契约），依赖 `faster-whisper` 入全局 requirements.in/lock |

### 2.3 解析失败降级（V2 风险 19.1 / 19.4）

| 条目 | 位置 | 修复内容 |
| ---- | ---- | ---- |
| 19.1 OCR/ASR 无降级 | `ingest_service.py` L61-68 | 新增开关 `KB_OCR_ASR_DEGRADED_KEEP_TEXT`（默认 false）：开启时解析失败不直接 A3001，走占位降级（status=failed + 失败日志 + degraded=true 标记）；关闭时与 V2 完全一致 |
| 19.4 ES 入库失败无降级 | `ingest_service.py` L142-145 | 新增开关 `KB_ES_DEGRADED_KEEP_MILVUS`（默认 false）：开启时保留 Milvus + kb_chunks + status=active + 异步补索引（指数退避 1s/2s/4s，30min 窗口）；关闭时与 V2 完全一致（A3003 + 回滚） |

### 2.4 行为偏差修复（V2 偏差 L8 / L15）

| 条目 | 位置 | 修复内容 |
| ---- | ---- | ---- |
| L8 缓存命中日志缺字段 | `api/search.py` L81-97 | 缓存命中分支补传 `query_original=req.query`、`query_rewritten`/`intent_tag`（无改写/路由结果则落 NULL） |
| L15 请求体 session_id 无效字段 | `models/search.py` | 移除 `SearchRequest.session_id`（代码只读 `X-Session-Id` 请求头，字段从未生效）；Pydantic 默认忽略未知字段，不破坏调用方 |

### 2.5 回溯修复（审计 D2 / D-1 / D5）

| 条目 | 位置 | 修复内容 |
| ---- | ---- | ---- |
| D2 rerank 加权主路径生效 | `search_service.py` L304-320 | 业务加权（`KB_WEIGHTING_ENABLED`）此前仅作用于 RRF 融合分，Rerank 正常路径按纯 `rerank_score` 重排导致加权被覆盖。修复：排序分 = `rerank_score × weight`（weight 缺省 1.0） |
| D-1 max_rerank_score 副作用闭环 | `search_service.py` L304-320 + `test_gate_threshold.py` | D2 曾使 `max_rerank_score` 误取加权排序首项原始分（0.34<0.35 误触兜底）。修复为取全局最大**原始** rerank_score（不乘权重），闸门 2 阈值语义不被加权排序污染；新增 `TestGate2ThresholdNotPollutedByWeighting` 2 用例（防误兜底 / 防误放行） |
| D5 include_answer 死字段清理 | `models/search.py` | 删除 V1-era 遗留死字段 `include_answer`（PRD §1.4 明确不接 LLM），无其他代码引用，对齐 PRD 与 hybrid-search spec |

### 2.6 配置 / 依赖 / 测试基建

- 新增 2 开关 ENV（`KB_OCR_ASR_DEGRADED_KEEP_TEXT`、`KB_ES_DEGRADED_KEEP_MILVUS`）：`settings.py` + Dockerfile + `.env.example`，默认 false
- `faster-whisper` 入全局 `requirements.in` / `requirements.lock`（对齐"全局单一依赖文件"约束）
- 测试基建：`tests/conftest.py` 新增 DB-backed `async_db_session` fixture（受 `RAG_DB_TESTS` 控制）；`test_0004_seed_consistency.py` 移除 skipif 恢复断言
- 新增 7 个测试文件/模块：`test_asr_vad`、`test_whisper_provider`、`test_row_access_fail_closed`、`test_redis_fault_tolerance`、`test_ingest_degradation`、`test_sensitive_seed_publish`、`test_gate_threshold`（新增类）、`test_logs_v2`/`test_search` 断言增强

---

## 三、Spec 对齐点（8 个 capability 全量对齐清单）

> 来源编号：V2 偏差（B/M/L）、V2 风险登记（19.x）、审计闭环项（D-*，见第四节）。全部已通过 `/opsx-sync` 合并进主 spec（`openspec/specs/rag-service/`）。

| Capability | 对齐内容 | 来源 |
| ---------- | -------- | ---- |
| **hybrid-search** | ① 兜底文案统一为代码文案「知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。」；② 降级策略补 try/except 熔断探测容错 + 新 Scenario「熔断探测 Redis 故障不连锁 500」；③ 闸门 1 删除 degraded=true 表述，仅保留 `is_fallback='1'`，degraded 收敛为"基础设施降级"（方案A）；④ 闸门 1/2 Scenario 同步（degraded 保持 false） | L7 / 19.2 / B3 / D-4 |
| **multimodal-ingest** | ① OCR `lang="ch"` 统一（消除 design/spec/code/README 三方不一致）；② ASR VAD 分片真实实现 + 「开场白裁剪」规格澄清（语义级 VAD 未实现，移交 V4）；③ 新增 Requirement「OCR/ASR 解析失败降级」（3 Scenario）；④ 「仅文本入库」超前描述收敛为占位入库（唯一实现策略）；⑤ `source` 字段记录值修正为 `"asr"`；⑥ 状态机补回滚契约句 | L1 / M1 / 19.1 / D-3 / D8 / D9 |
| **smart-retrieval-pipeline** | ① 路由权重场景对齐 YAML（case:1.0 / other:0.6）；② 动态路由 Requirement 重写：权重 YAML 为唯一事实源 + D2 权重作用范围说明（排序分 = rerank_score × weight）+ Scenario 对齐 | L4 / D2 |
| **search-logging** | ① 缓存命中分支补传 query_original/query_rewritten/intent_tag；② D6 边界：缓存命中场景 query_rewritten/intent_tag 落 NULL（可空字段契约）；③ 新增 2 个缓存命中 Scenario（落空 / 三字段落空） | L8 / D6 |
| **search-enhancements** | 行级权限过滤 fail-closed 三段式（error 日志 + 保底矩阵重放 + 空结果走兜底）+ 新 Scenario「权限矩阵配置损坏时 fail-closed 保底」 | 19.3 / D1 |
| **sensitive-words-sync** | ① 启动自检补 ④ PUBLISH（seed 初始化触发版本事件）+ D7 实现备注；② Pub-Sub 日志键统一为代码实际键 `sensitive_baseline_*` 系列（**以代码为准，拒绝 delta 错误键名**）；③ seed description 语义修正（三处 description 各自承载语义，不再声明完全相同） | B2 / L2 / L3 / W1 |
| **conversational-search** | ① 请求体可选 `session_id` 字段已移除（对外仅 `X-Session-Id` 请求头）；② 会话读写 MUST 纳入 Redis 容错（不抛异常、不导致检索 500）；③ 新增 2 Scenario（请求体携带 session_id 被忽略 / 会话读取异常不 500） | L15 / 19.2 |
| **document-ingest** | ① 格式白名单补 `.jpeg` + 新 Scenario「上传 JPEG 扩展名图片成功」；② **ADDED** Requirement「ES 入库失败降级」（KB_ES_DEGRADED_KEEP_MILVUS，3 Scenario：降级仅 Milvus 可检索 / 窗口内重试失败 warning / 开关关闭=V2 失败路径） | L6 / 19.4 |

---

## 四、审计交叉比对闭环（tasks §10 归档备注 D/B 项 + W 项）

> 说明：本节 D 编号为**审计闭环记录**编号（tasks.md §10），与 design.md 的 Design Decisions（D1-D13）是两套独立编号体系；两者关系已在对应条目注明。W 项为多轮只读审计发现的文档漂移。

| 编号 | 条目 | 状态 | 说明 |
| ---- | ---- | ---- | ---- |
| D2 | rerank 加权主路径生效 | ✅ 闭环 | 代码修复 + tasks 登记（对应 design D12；含 D-1 副作用修复，见 2.5） |
| D4 | 请求体 session_id 字段移除 | ✅ 闭环 | 代码 + spec 对齐（对应 design D9） |
| D5 | include_answer 废弃字段清理 | ✅ 闭环 | 代码删除 + spec 归档标记（对应 design D13） |
| D6 | 缓存命中日志字段允许 NULL | ✅ 对齐 | search-logging spec 边界说明（对应 design D8） |
| D7 | 敏感词 seed 分支已关闭 session 构造 | 📌 移交 V4 | 仅 `publish_version` 不触 DB 故功能正常，复用已关闭旧 session 属遗留待重构（见 tasks 3.2 备注） |
| D8 | ASR「开场白裁剪」未实现 | 📌 移交 V4 | 静音裁剪已实现（RMS 阈值），开场白识别需语义级 VAD，规格澄清为超前表述 |
| D9 | OCR lang 统一为 "ch" | ✅ 闭环 | design/spec/code/README 三方一致 |
| D10 | compose 依赖说明 | ✅ 备注 | rag-service 走宿主/k8s 部署，`depends_on ocr-service` 仅为测试环境条件 |
| D11 | V2 封版偏差说明为归档快照 | ✅ 备注 | B2「V2 不实现 PUBLISH」为 V2 快照，V3 已实现，以 V3 为准 |
| B1 | V2 归档 tasks 完成度标记与代码未达并存 | ✅ 闭环 | V3 已全量实现（SIGHUP 热重载 / seed 自检 / 0006 清理），归档工件保留历史可追溯 |
| B2 | V2 归档 tasks §19 风险登记闭环 | ✅ 闭环 | 19.1-19.4、19.6 已由 V3 修复；19.5（LLM 摘要）无实施对象，仅设计约束记录 |
| B3 | 闸门 1 degraded 语义不一致 | ✅ 对齐（方案A） | spec 删除闸门 1 的 degraded=true，仅保留 is_fallback='1'；代码行为不变（degraded 保留基础设施降级语义） |
| W1 | 敏感词 spec 日志键名漂移 | ✅ 修复 | 3 处 Scenario 日志键统一为代码实际键 `sensitive_baseline_*`（main.py 9 键：count_failed/dict_read_failed/recount_failed/seed_success/version_init_failed/publish_failed/below_threshold/ok/init_failed） |
| W2 | design.md 缺 D2 Decision 记录 | ✅ 补齐 | 补 rerank 加权主路径生效的 Decision 记录（design D12） |
| W3 | design.md 缺 D5 Decision 记录 | ✅ 补齐 | 补 include_answer 清理的 Decision 记录（design D13） |
| W4 | 历史文档漂移（部分） | 📌 后续迭代 | 按约定标记后续迭代承接，不并入 V3 主 spec |
| W5 | 历史文档漂移（部分） | 📌 后续迭代 | 同上 |
| W6 | 历史文档漂移（部分） | 📌 后续迭代 | 同上 |

---

## 五、遗留项移交记录

### 5.1 移交 V4 迭代（功能遗留，非 V3 阻断）

| 编号 | 遗留项 | 说明 |
| ---- | ------ | ---- |
| D7 | 敏感词 seed 分支已关闭 DB session 构造 | `main.py` seed 分支复用已关闭旧 session 构造 `SensitiveWordsSyncService`，仅 `publish_version` 能力正常，需重构 |
| D8 | ASR「通话开场白」裁剪 | 静音裁剪已实现；开场白识别需语义级 VAD（VAD 分片 + 语义识别），V3 未实现 |

### 5.2 移交后续治理变更（Non-Goals，DD-03 §11.4 V3-T1~T5）

| 编号 | 条目 | 优先级 |
| ---- | ---- | ---- |
| V3-T1 | LangSmith 追踪接入（与 PG 日志并存） | P1 |
| V3-T2 | 评估模块（BadCase 回流 + 指标看板） | — |
| V3-T3 | 文档版本管理 + 灰度发布 | — |
| V3-T4 | Prompt 动态配置 | — |
| V3-T5 | 敏感词 CRUD 管理 | — |
| 19.5 | LLM 摘要生成模块及其降级链 | 设计约束记录（无实施对象） |

### 5.3 历史文档漂移（W4 / W5 / W6）

已标记"后续迭代承接"并记录于审计报告；不构成 V3 封版阻断。

---

## 六、验证与回归结果

| 验证项 | 结果 |
| ------ | ---- |
| 单元测试 | **181 passed / 1 skipped**（RAG_DB_TESTS 条件跳过，基线 150 passed 增长 31 用例） |
| openspec validate | 变更工件 validate 通过；合并后主 spec `--specs` **8 passed / 0 failed** |
| 冒烟主链路 | smoke 主链路验证完成（smoke_rag.sh 全 PASS；smoke_rag_v2.sh Step1-11 含 Step11 回归自检 PASS；Step10 保持原样） |
| 静态检查 | ruff/bandit/mypy 不劣于基线（F401 清零） |
| 开关默认值 | 新增 2 开关默认 false，关闭时检索/入库行为与 V2 逐项一致 |

---

## 七、兼容性保障与回滚

- **开关隔离**：V3 全部行为改动以开关接入（V2 既有 7 开关体系 + 新增 2 开关），默认 false；关闭开关 = V2 完全行为
- **契约不变**：对外接口路径、统一响应包 `{"code":"0",message,data,trace_id}`、错误码全集均不变（未新增错误码）
- **无 schema 变更**：无数据库 DDL 变更、无 Alembic 迁移脚本
- **回滚成本为零**：关闭新增开关即可恢复 V2 行为；代码增量分支不触碰既有逻辑

---

## 八、封版确认清单

- [x] D-1 副作用修复闭环（`max_rerank_score` 取全局最大原始分，2 个回归用例）
- [x] D-4 闸门 1 degraded 语义按方案A完成 spec 对齐（代码未改动）
- [x] D-3「仅文本入库」超前描述收敛（占位入库为唯一实现策略）
- [x] W1 敏感词日志键以代码为准修复（拒绝 delta 错误键名）
- [x] design.md D2 / D5 Decision 决策记录补齐
- [x] tasks.md 全部勾选（0 未完成），D2 归档登记补齐
- [x] 8 个 delta spec 与主 spec 一致性验证通过（validate 8/8）
- [x] 变更已归档 → `openspec/changes/archive/2026-08-24-rag-multimodal-v3/`

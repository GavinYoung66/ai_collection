# RAG 多模态知识库 V4 — 摘要元数据与检索增强：技术设计

## Context

RAG 多模态知识库 V1/V2/V3 已归档，8 个主 spec 在 `openspec/specs/rag-service/`，9 个行为开关（全部默认 false）承载灰度，检索主链路（双路召回 → RRF → Rerank → 双闸门 → 行级权限 → 日志/缓存）稳定，测试基线 181 passed / 1 skipped。V4 动机与范围见 proposal.md；本设计只解释"怎么做"。

存量约束（设计必须遵守）：

- 分层单向依赖（Controller → Service → Repository），错误码全集 84 条禁新增，统一响应包 `{"code":"0",message,data,trace_id}`，测试门禁 ≥85%、合规红线 100%。
- **PRD §1.4 硬边界**：RAG-Service 内部禁止发起任何 LLM 调用；不生成面向坐席/终端用户的业务话术；`include_answer` 类生成式应答字段不得回归。
- 增量开发：不修改 V1/V2/V3 对外接口、数据模型既有列语义、既有业务逻辑；新能力全部以开关接入，默认 false。

## Goals / Non-Goals

**Goals:**

- 落地"摘要元数据"能力：上游预生成摘要 → 注入（上传携带 + 独立补全）→ 两端存储（PG 权威 + ES 检索副本）→ 检索增强（ES 摘要字段加权）→ 响应返回（`summary` 字段永远存在，开关控制填充），全程 RAG 内部零 LLM 调用。
- 闭环 V1-V3 遗留：D7（seed 分支 session 重构）、D8（ASR 开场白规则裁剪）、W4/W5/W6（历史文档漂移修正）。
- 全量向后兼容：存量调用方零改造；新增开关默认 false，关闭 = V3 行为。

**Non-Goals:**

- 不做 chunk 级摘要（每 chunk 各自语义摘要，需按分块语义生成，超出上游预生成约束）。
- 不做 Milvus 独立摘要向量 collection 与向量级摘要聚合检索（V4 用 ES 字段加权 + PG 权威源，Milvus schema 保持不变；聚合检索入 Backlog）。
- 不做治理能力（版本管理/评估/LangSmith/Prompt 配置/敏感词 CRUD）与 A13 数据管道——见 proposal Non-Goals。
- 不引入任何 LLM 供应商依赖、不新增错误码、不修改既有接口契约。

## Decisions

### D1. 摘要来源：上游预生成 + 注入渠道（冲突决议落地）

- **决议**：摘要定位为**检索增强元数据**，文本 MUST 由上游业务中台在文档入库阶段调用大模型预生成，经注入渠道提交；RAG-Service 内部不生成摘要、不调 LLM。
- **渠道**：
  1. `POST /kb/documents` 表单新增可选字段 `summary`（同步主路径，适合文本类文档）；
  2. `PUT /kb/documents/{doc_id}/summary`（独立补全，适合多模态异步链路——图片 OCR / 音频 ASR 完成后业务中台才能生成摘要，此时后补）。
- **备选（放弃）**：
  - RAG 内部调 LLM 生成摘要 → 违反 §1.4、引入 LLM 不确定性治理与数据出网合规风险、成本不可控；
  - 上传时不携带、RAG 异步事后自生成 → 同上，且 RAG 无文本理解能力；
  - 仅上传携带、无补全接口 → 多模态异步场景无解，存量文档无法补齐，能力不完整。

### D2. 摘要粒度：文档级（doc_summary），非 chunk 级

- **决策**：V4 只支持文档级摘要。原因：上游业务中台在入库阶段可预知整篇文档语义、但无法预知 RAG 的分块边界，chunk 级摘要需要按分块生成，超出"上游预生成"约束。
- **备选（放弃）**：chunk 级摘要 → 需 RAG 内理解分块语义（违反 §1.4）或上游与分块逻辑耦合，入 Backlog。

### D3. 两端存储与写入顺序契约（防脏状态）

| 端            | 载体           | 字段          | 类型                | 可空            | 说明                                                                        |
| ------------- | -------------- | ------------- | ------------------- | --------------- | --------------------------------------------------------------------------- |
| PostgreSQL    | `kb_documents` | `summary`     | TEXT                | 是（默认 NULL） | 摘要**权威源**；存量不强制回填；Alembic 迁移 0007                           |
| Elasticsearch | `kb_bm25`      | `doc_summary` | text（ik_max_word） | 是              | 摘要**检索副本**，参与 BM25 加权；`es_v4_mapping.json` + `reindex_es_v4.py` |

Milvus `kb_vectors` **保持 V3 schema 不变**：V4 不向 chunk 向量 metadata 冗余文档级摘要（避免一次性重建索引的发布窗口成本；摘要召回诉求由 ES 字段加权承载，向量级聚合检索入 Backlog）。

**入库写入顺序（正文优先、摘要尽力而为）**：

```
上传(file, category, summary?) → 权限/限流 → 校验（summary ≤ 200 字符，超限 B0001）
→ 既有 10 步正文入库（解析/分块/向量化/双索引/kb_chunks/status=active）
→ 若携带 summary：写 ES doc_summary → 写 PG kb_documents.summary
→ ES 写失败：warning（含 trace_id）+ PG summary 不写（保持 NULL），正文已 active，不返回错误
```

关键：**PG 的 summary 是"一致性收口标记"**——ES 检索副本写成功才落 PG 权威源，从根上杜绝"PG 有摘要、索引无摘要"的脏状态；索引失败时正文不受影响。

**补全接口写入顺序（ES → PG，失败重试）**：

```
PUT {doc_id, summary} → 权限/限流 → 校验 → 校验文档存在（否则 B0004）
→ 写 ES doc_summary（失败重试 3 次：1s/2s/4s）→ 写 PG kb_documents.summary
→ ES 重试仍失败：PG 不写（无半状态），返回失败（复用 A3003 语义，见下注）
→ 全部成功：失效该文档 category 的查询缓存，返回 code=0
```

ES 更新是幂等的（按 doc_id 重写 `doc_summary` 字段），回滚成本低。

> **错误码语义注**：在"错误码全集 84 条禁新增"约束下，补全接口索引写入失败复用 A3003（向量入库失败，语义最接近"写入索引失败"）；该复用为显式权衡，不新增错误码，不改变 A3003 在既有向量入库路径中的语义。

### D4. 检索增强：ES `doc_summary` BM25 加权，不做向量级摘要召回

- **开关**：`KB_SUMMARY_ENABLED`（默认 false）+ `KB_SUMMARY_ES_BOOST`（默认 1.0）。
- **行为**：开关开启时，ES 路召回 query 对 `content` 与 `doc_summary` 双字段检索，`doc_summary` 权重 = `KB_SUMMARY_ES_BOOST`；摘要命中高的文档在 ES 路排序中上浮，经 RRF(k=60) 融合后自然影响最终结果。正文语义召回（Milvus 路）不变。检索响应 `results[]` 每项 `summary` 字段**永远存在**（契约稳定）：开关开启时填充该文档的文档级摘要（无摘要为 null），开关关闭时一律为 null（不填充，检索语义与排序同 V3 完全一致）。
- **为何不做向量级摘要召回**：Milvus 为 chunk 级向量，文档级摘要向量需新增独立 collection（`kb_doc_summaries`）与聚合排序链路，重建/增量/删除/权限过滤全链路都要适配，复杂度高；ES 字段加权已覆盖"多模态内容语义稀疏 → 摘要兜底命中"的核心诉求。向量级摘要聚合检索入 Backlog，作为 P2 可选增强。
- **备选（放弃）**：Rerank 后按摘要相关性二次重排 → 引入二次排序与权重体系，收益不确定，V4 不做。

### D5. 检索链路其余环节零改动

RRF 融合、Rerank 精排、双闸门兜底（0.6/0.35）、行级权限过滤（fail-closed）、同 doc 去重、缓存 key（含开关段）、kb_search_logs 落库、限流/权限切面——**全部保持 V3 语义不变**。摘要仅在"ES 召回加权"与"响应字段"两处介入，且以开关隔离。

### D6. ASR 通话开场白规则裁剪（D8 落地）

- **实现**：转写文本拼接完成后，按 `conf/asr_opening_rules.yaml`（正则 + 关键词表：问候语/身份确认/公司名模式）识别文本开头的催收开场模式，裁剪至首个有效业务内容起点。
- **开关**：`KB_ASR_OPENING_TRIM_ENABLED`（默认 false），关闭 = V3 行为。
- **边界**：纯规则实现，零 LLM、零新第三方依赖；语义级 VAD（说话人/意图级开场识别）仍为 Backlog。
- **备选（放弃）**：引入 webrtcvad/自研语义 VAD → 语义级开场识别无轻量方案，且 V3 已按"静音裁剪 RMS 阈值"实现物理层去静音，开场白语义裁剪用规则足够覆盖"喂/你好/请问是"这类固定模式。

### D7. seed 分支 session 重构（D7 遗留）

`main.py` 敏感词 seed 分支当前复用已关闭的旧 DB session 构造 `SensitiveWordsSyncService`（V3 仅 `publish_version` 正常）。重构为：seed 分支在 startup 生命周期内显式持有有效 session 并传入，或改用不依赖 session 的静态方法路径；不改变 seed 自检/PUBLISH 的外部行为。

### D8. 历史文档漂移修正（W4/W5/W6）

按 V3 封版审计记录的漂移清单，逐条核对 spec/README/代码注释与代码实际行为的一致性并修正（本变更承接 V3 遗留的 W4/W5/W6 项，具体条目在 tasks.md 中列明核对范围）。

**修正清单（任务 5.3 执行记录）**：

| #   | 位置                                                                           | 漂移内容                                                                                                                                                            | 修正动作                                                                     |
| --- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1   | `openspec/specs/rag-service/sensitive-words-sync/spec.md`（D7 实现备注）       | 备注称"本次仅记录，不重构"，但 V4 任务 5.1 已将 `publish_version` 静态化并移除对旧 session 的复用                                                                   | 更新备注为"审计 D7，V4 已闭环"，描述静态方法路径                             |
| 2   | `openspec/specs/rag-service/multimodal-ingest/spec.md`（规格澄清 D8）          | 澄清称"通话开场白裁剪留待后续迭代"，但 V4 任务 5.2 已以规则方式实现（`conf/asr_opening_rules.yaml` 纯正则+关键词表，零 LLM）                                        | 更新澄清为"D8 已落地"，注明开关门控/保底逻辑，语义级 VAD 仍为 Backlog        |
| 3   | `services/rag-service/app/services/query_cache.py`（`_flags_hash` 注释与行为） | 缓存隔离 hash 仅收录 V2 7 个开关，未纳入 V4 `KB_SUMMARY_ENABLED`；开关切换后旧缓存（1h TTL）会违反"开启填充摘要/关闭恒为 null"契约（spec summary-metadata 3.6/3.7） | 将 `KB_SUMMARY_ENABLED` 纳入 flags hash，更新模块/函数/`make_cache_key` 注释 |
| 4   | `services/rag-service/README.md`（标题）                                       | 标题为"V2 多模态知识库"，落后于 V3/V4 演进（V2 归档遗留措辞）                                                                                                       | 更新为"V4 多模态知识库，摘要元数据能力 + 开场白规则裁剪"                     |

核对范围：8 个主 spec（conversational-search / document-ingest / hybrid-search / multimodal-ingest / search-enhancements / search-logging / sensitive-words-sync / smart-retrieval-pipeline）与 `README.md`、代码实际行为。核对结论：兜底文案、溯源 source/version 缺省、闸门 1 degraded 语义、缓存命中三字段落空、请求体 session_id 移除等条目与代码实现一致，无漂移。

## Risks / Trade-offs

- [ES mapping 变更需 reindex] → 提供 `es_v4_mapping.json` + `reindex_es_v4.py`（增量重建 `doc_summary` 字段）；reindex 完成前摘要增强开关不开启，正文 BM25 检索不受影响。
- [补全接口两端一致性] → ES 先写 + 失败重试 3 次 + PG 收口；ES 重试仍失败则 PG 不写，无半状态；PG 写入失败仅 warning + degraded 标记，由运维人工介入（幂等重试可收敛）。
- [摘要质量依赖上游] → RAG 仅做长度/类型校验，不做语义校验；摘要质量由业务中台保证，坏摘要最多影响加权召回排序，不产生合规风险（摘要非话术输出）。
- [开场白规则误裁] → 关键词表保守设计 + 开关默认 false；规则裁剪后文本为空则不裁剪（保底），不误删有效业务内容。
- [响应新增 summary 字段] → JSON 新增可空字段对存量调用方完全兼容（Pydantic 默认忽略未知字段的对称行为：服务端新增字段不影响旧客户端解析）。

## API 变更对照表（新增/不变）

| 接口                                                                            | 变更类型             | 说明                                                                                                                                               |
| ------------------------------------------------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /kb/documents`                                                            | 新增 form 字段       | `summary` 可选，超 `KB_SUMMARY_MAX_LEN`（默认 200）返回 B0001；不携带 = 存量行为完全一致                                                           |
| `PUT /kb/documents/{doc_id}/summary`                                            | **新增接口**         | ADMIN/SUPERVISOR，限流 10/min；请求体 `{"summary": str}`；doc_id 不存在/archived → B0004；ES 写失败重试 3 次后返回失败（复用 A3003 语义），PG 不写 |
| `GET /kb/documents/{doc_id}`                                                    | 新增响应字段         | 详情响应新增 `summary`（可空，来源 PG 权威源）                                                                                                     |
| `POST /kb/search`                                                               | 新增响应字段         | `results[].summary` 永远存在（可空）；开关开启填充摘要，开关关闭为 null                                                                            |
| `GET /kb/documents`（列表）                                                     | **完全保持原有不变** | 不新增 summary 字段，响应负载不变                                                                                                                  |
| `DELETE /kb/documents/{doc_id}`、reindex、sensitive-words、`/health` 等其余接口 | **完全保持原有不变** | 路径、请求/响应包、错误码零改动                                                                                                                    |

## Performance（非功能需求）

- **查询侧**：摘要增强仅修改 ES 路 query（追加 `doc_summary` 字段加权），不新增外部调用、不增加召回路数；既有 `RAG_P95_TARGET=3s` 目标不变；开关关闭时零额外开销（ES query 与 V3 完全一致）。
- **写入侧**：上传携带摘要 = 既有正文 10 步入库 + 追加 ES 1 次写入 + PG 1 次更新；补全接口 = ES 1 次写入（失败重试 3 次）+ PG 1 次更新；外部写入均带超时，失败不阻塞主链路吞吐。
- **存储与缓存**：摘要仅存 PG（权威）与 ES（检索副本），Milvus 不新增字段、不重建索引；补全后按 category 失效查询缓存（复用既有失效机制），不破坏 step6/02 缓存命中率监控与告警逻辑。

## Migration Plan

1. **数据库**：Alembic 迁移 `0007_kb_documents_summary.py`（`kb_documents.summary` TEXT NULL，不强制回填）。
2. **ES**：`scripts/rag/es_v4_mapping.json` + `scripts/rag/reindex_es_v4.py` 增量重建 `doc_summary` 字段。
3. **Milvus**：schema 不变，无重建操作（V4 摘要不写入向量库）。
4. **配置**：`KB_SUMMARY_ENABLED` / `KB_SUMMARY_ES_BOOST` / `KB_SUMMARY_MAX_LEN`（默认 200）/ `KB_ASR_OPENING_TRIM_ENABLED` 定义于 `common/config/settings.py`（供全部 7 服务共用），Dockerfile ENV / `.env.example` 同步；`conf/asr_opening_rules.yaml` 新增。
5. **代码**：按 tasks.md 增量实现（接口 → 存储 → 检索 → 遗留修复 → 文档注释 → 回归）。
6. **回滚**：全部行为改动以开关隔离，关闭新增开关即恢复 V3 行为；PG 列可空、ES 只增字段，回滚零成本、无 schema 回退。

## Open Questions

- 无阻塞性设计问题。`KB_SUMMARY_ES_BOOST` 默认值 1.0（与正文同权）是否需按实测调优，留待 apply 后冒烟/评测阶段用数据决策，不影响本变更的 spec 与任务拆分。

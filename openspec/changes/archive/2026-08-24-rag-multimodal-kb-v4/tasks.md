# RAG 多模态知识库 V4 — 任务清单

> 实施原则：增量开发，不改动 V1/V2/V3 已确认核心业务逻辑与对外契约；新能力以开关接入（默认 false），关闭开关 = V3 完全行为。每项任务标注【新增】/【修改】及文件路径；修改已有文件标注具体位置与改动原因。参考 spec（summary-metadata / multimodal-ingest delta）与 design.md。

## 1. 配置与开关

- [x] 1.1 【修改】`common/config/settings.py`：新增 `KB_SUMMARY_ENABLED`（默认 False）、`KB_SUMMARY_ES_BOOST`（默认 1.0）、`KB_SUMMARY_MAX_LEN`（默认 200）、`KB_ASR_OPENING_TRIM_ENABLED`（默认 False）四个配置项（与既有 KB\_\* 开关同级，供全部 7 服务共用），附类型注解与注释
- [x] 1.2 【修改】`services/rag-service/Dockerfile`：新增上述 4 个 ENV（默认值同 settings）；`.env.example` 同步登记
- [x] 1.3 【新增】`services/rag-service/conf/asr_opening_rules.yaml`：开场白规则配置（问候语/身份确认/公司名模式关键词 + 正则，保守设计），含注释说明触发与保底逻辑
- [x] 1.4 【修改】`services/rag-service/README.md`：开关清单补 4 个新开关（对齐既有开关登记风格）

## 2. 数据模型与存储适配

- [x] 2.1 【新增】`migrations/versions/0007_kb_documents_summary.py`：`kb_documents` 增列 `summary` TEXT NULL（可空、不强制回填，版本号接续既有最大 revision）
- [x] 2.2 【修改】`services/rag-service/app/models/orm.py`：`KbDocument` ORM 增 `summary` 字段（可空），与迁移对齐
- [x] 2.3 【核对】`services/rag-service/app/vectorstore/milvus_client.py`：确认 `kb_vectors` 保持 V3 schema 不变（V4 摘要不新增 Milvus 字段、不向 chunk 向量 metadata 冗余），无需重建索引
- [x] 2.4 【新增】`scripts/rag/es_v4_mapping.json` + `scripts/rag/reindex_es_v4.py`：`kb_bm25` 增 `doc_summary`（text，ik_max_word）并增量重建
- [x] 2.5 【修改】`services/rag-service/app/vectorstore/es_client.py`：insert/update 支持 `doc_summary` 字段读写，保持既有方法签名向后兼容（缺省空）；Milvus 客户端不做改动

## 3. 接口开发

- [x] 3.1 【修改】`services/rag-service/app/api/documents.py`：上传接口 `POST /kb/documents` 表单解析可选字段 `summary`；超 `KB_SUMMARY_MAX_LEN` 返回 B0001；不携带 = 存量行为完全不变
- [x] 3.2 【新增】`services/rag-service/app/api/documents.py`：新增接口 `PUT /kb/documents/{doc_id}/summary`（权限 ADMIN/SUPERVISOR，限流 10/min），请求体 `{"summary": str}`，校验后走补全服务（3.3）；doc_id 不存在/archived → B0004
- [x] 3.3 【新增】`services/rag-service/app/services/summary_service.py`：摘要两端写入编排（ES → PG 顺序；ES 失败 3 次退避重试，重试仍失败则 PG 不写并返回失败（复用 A3003 语义）；全部成功失效该文档 category 查询缓存），供上传入库与补全接口复用
- [x] 3.4 【修改】`services/rag-service/app/models/document.py`：新增 `SummaryUpdateRequest`（summary: str, Field(max_length=KB_SUMMARY_MAX_LEN)）；`DocumentDetailResponse` 增 `summary` 字段（可空，默认 None）
- [x] 3.5 【修改】`services/rag-service/app/api/documents.py`：`GET /kb/documents/{doc_id}` 详情响应返回 `summary`（列表接口不新增字段，保持负载不变）
- [x] 3.6 【修改】`services/rag-service/app/models/search.py`：`SearchResponse.results[]` 项新增 `summary: str | None = None` 字段（永远存在，可空；`KB_SUMMARY_ENABLED=true` 时填充摘要值，关闭时恒为 null）
- [x] 3.7 【修改】`services/rag-service/app/api/search.py`：响应组装填充各结果项 `summary`（`KB_SUMMARY_ENABLED=true` 时取 PG 文档级摘要，缺失为 null；关闭时恒为 null），不改变既有检索逻辑

## 4. 检索增强

- [x] 4.1 【修改】`services/rag-service/app/repositories/doc_repo.py`：新增 `get_summary(doc_id)` / `update_summary(doc_id, summary)`（供补全与响应组装）
- [x] 4.2 【修改】`services/rag-service/app/services/ingest_service.py`：正文入库成功后，若携带 summary 调用摘要写入（顺序见 design D3：ES → PG）；ES 写失败仅 warning（含 trace_id）+ PG summary 不写，正文已 active 不回滚
- [x] 4.3 【修改】`services/rag-service/app/services/search_service.py`：ES 路召回在 `KB_SUMMARY_ENABLED=true` 时对 `doc_summary` 字段 BM25 加权（权重 `KB_SUMMARY_ES_BOOST`）；开关关闭时 ES query 与 V3 完全一致；不触碰 RRF/Rerank/双闸门/权限/去重/日志/缓存逻辑

## 5. 遗留问题修复

- [x] 5.1 【修改】`services/rag-service/app/main.py`：D7 修复——敏感词 seed 分支不再复用已关闭的旧 DB session 构造 `SensitiveWordsSyncService`，改为 startup 生命周期内显式持有有效 session 或抽取不依赖 session 的静态方法路径；seed 自检/PUBLISH 外部行为不变
- [x] 5.2 【修改】`services/rag-service/app/services/asr_client.py`：D8 修复——转写文本拼接后执行开场白规则裁剪（读 `conf/asr_opening_rules.yaml`，`KB_ASR_OPENING_TRIM_ENABLED=true` 生效）；裁剪后文本为空则不裁剪（保底）；开关关闭 = V3 行为；不引入 LLM/新依赖
- [x] 5.3 【修改】W4/W5/W6 文档漂移修正：逐条核对 `openspec/specs/rag-service/` 8 个主 spec、`services/rag-service/README.md`、代码注释与代码实际行为的一致性，修正发现的漂移（含 V2 归档遗留措辞），记录修正清单（详见 design.md D8 修正清单表：D7 备注闭环、D8 澄清落地、flags_hash 缓存隔离、README 标题 4 项）

## 6. 文档注释补齐（对齐约束：代码 / 接口文档 / 需求规范三方一致）

- [x] 6.1 【修改】新增/修改的 Pydantic 模型字段（`summary`、`SummaryUpdateRequest` 等）补完整 docstring：字段含义、类型、可空、来源（`SourceItem.summary`、`DocumentDetailResponse.summary` 升级为 Field(description=...) 含来源与开关语义）
- [x] 6.2 【修改】新增/修改的 API（上传 summary、`PUT .../summary`、详情/检索响应）补 OpenAPI docstring（summary/description/response 说明），标注"新增"与"存量不变"（upload_document / get_document / hybrid_search 已补 docstring，update_document_summary 已有）
- [x] 6.3 【修改】`services/rag-service/README.md`：新增 V4 摘要能力章节（功能说明、API 变更表、数据模型变更、迁移 Runbook、回滚说明），与 design.md 对齐（新增 §D 章节）

## 7. 测试与回归

- [x] 7.1 【新增】`tests/test_summary_metadata.py`：上传携带摘要入库 → PG/ES 两端一致（Milvus 不含摘要字段）；不携带摘要 → 存量行为不变
- [x] 7.2 【新增】`tests/test_summary_metadata.py`：补全接口成功（两端同步 + 缓存失效）；doc_id 不存在 → B0004；ES 写失败重试后返回失败且 PG 不写（无半状态）
- [x] 7.3 【新增】`tests/test_summary_metadata.py`：检索增强开关开启 → ES 摘要加权生效、响应 `results[].summary` 填充摘要值；开关关闭 → 检索行为与 V3 一致且 `summary` 恒为 null（基线比对）
- [x] 7.4 【新增】`tests/test_summary_metadata.py`：入库时摘要写失败（ES mapping 未更新模拟）→ 正文 active、PG summary 为 NULL、warning 日志含 trace_id、无 A3003
- [x] 7.5 【新增】`tests/test_asr_opening_trim.py`：开场白规则裁剪开启/关闭两态 + 无开场白音频不误删 + 裁剪后为空保底不裁
- [x] 7.6 【回归】全量单测：基线 181 passed / 1 skipped 不回退（实测 209 passed / 1 skipped = 基线 181 + 新增 28）；新增用例全绿；ruff/bandit/mypy 不劣于基线（rag-service 目录 ruff 0 错误；bandit/mypy 全部 issue 为既有基线，V4 新增文件零违规）
- [x] 7.7 【回归】冒烟：`smoke_rag.sh` 保持原样全 PASS；新增 V4 冒烟步骤（上传携带摘要 → 详情返回摘要 → 补全 → 检索返回摘要 → 降级不阻断）并入 `smoke_rag_v4.sh`（15 步：Step 1-11 沿用 V2 链路 + Step 12-15 V4 摘要能力，bash -n 语法校验通过）
- [x] 7.8 【回归】`scripts/rag/check_error_codes.sh` / `check_nongoals.sh` 无违规（实测：错误码白名单 0 违规、Non-Goals 防呆 0 违规）

## 8. 封版与同步

- [x] 8.1 `openspec validate` 本变更工件校验通过（实测：Change 'rag-multimodal-kb-v4' is valid）
- [x] 8.2 8 个主 spec 与 2 个 delta spec 一致性验证（实测 `openspec validate --specs`：8 passed, 0 failed）
- [x] 8.3 【归档】评审通过后经 `/opsx-sync` 将 delta 合入主 spec，变更归档至 `openspec/changes/archive/`（实测：delta 已合入 multimodal-ingest / summary-metadata 主 spec，9 个主 spec validate 通过，变更已归档至 `openspec/changes/archive/2026-08-24-rag-multimodal-kb-v4/`）

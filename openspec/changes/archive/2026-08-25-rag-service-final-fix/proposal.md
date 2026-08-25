# rag-service-final-fix — Proposal

## Why

RAG-Service 完成 V1→V4 四轮迭代后，全量终局静态交叉评审（需求文档 DD-03 × OpenSpec 主规范 × 最终源码）发现 6 项 A 类阻断问题与 8 项潜在隐患：上传无大小限制可致内存溢出、解析无超时可长时间阻塞、/health 静态返回导致依赖故障不可感知、reindex 会破坏 V4 摘要两端一致性契约、删除失败可能残留向量/索引等。上述问题影响生产就绪标准，需在本次变更中统一修复，使文档（DD-03）与代码行为完全对齐。

## What Changes

**A 类阻断（6 项）：**

- **top_k 边界文档对齐（仅文档）**：DD-03 §5.2 入参边界与 §10.3 永久性约束措辞修订为 `top_k∈[1,20]`（与 `models/search.py` 的 `ge=1, le=20` 对齐），并在 hybrid-search 主 spec 定稿边界，消除三方漂移；不改代码。
- **上传大小上限**：`common/config/settings.py` 新增 `KB_MAX_FILE_SIZE=20MB`（对齐 DD §8），上传超限返回 B0001，并在读入内存前完成校验。
- **解析超时**：`settings.py` 新增 `KB_PARSE_TIMEOUT=30s`（对齐 DD §6.1/§8），parsers 调用处统一 `asyncio.wait_for`，超时按既有解析失败路径（A3001/降级）处理。
- **/health 依赖探活**：实现 PostgreSQL/Milvus/Redis/Embedding 四者真实探活（当前静态返回），统一双 /health 端点为单一实现。
- **reindex 摘要一致性**：reindex 重建 ES 时从 PG 回读 `summary` 写回 `doc_summary`，杜绝「PG 有摘要、索引无摘要」脏状态（对齐 summary-metadata 契约）。
- **重复删除语义文档对齐（仅文档）**：DD-03 §5.1 修订为「重复删除返回 B0004」（与代码行为、document-ingest 主 spec 一致）；不改代码。

**隐患项修复（8 项）：**

- 临时文件泄漏：`save_upload_file` 增加显式清理（`delete=True` 或 finally unlink）。
- Whisper 模型懒加载并发竞争：加线程锁或异步初始化，避免并发首请求重复加载。
- ASR Provider Redis 熔断探测异常未捕获：捕获并记录 warning，不向链路传播。
- 删除失败残留向量/索引：`delete_by_doc` 增加失败重试/补偿，保证删除幂等收敛。
- ES 降级补索引任务无持久化：改为记录任务状态，进程重启后可恢复。
- 文档列表 N+1 查询：批量获取 chunk 计数。
- 检索召回非法 UUID 导致 500：捕获 ValueError 返回 B0001。
- 全局客户端无 shutdown 清理：FastAPI shutdown 钩子显式关闭连接。

**文档同步（随附，仅文档）：**

- DD-03 §6.4 闸门 1「标记 degraded」修订为「degraded 保持 false（非基础设施降级）」。
- DD-03 §3 目录结构更新至 V4 快照；health.py 职责描述改为依赖探活。
- DD-03 §9 测试用例数 77 → 209 passed / 1 skipped。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `rag-service/document-ingest`: 新增「上传大小上限」「解析超时」「健康检查依赖探活」「删除失败重试/补偿」「ES 降级补索引任务持久化」Requirements；明确「重复删除返回 B0004（非幂等）」为既有行为约定。
- `rag-service/hybrid-search`: 新增/定稿「top_k 入参边界 [1,20]」与「非法 doc_id/UUID 入参返回 B0001」Requirements。
- `rag-service/summary-metadata`: 新增「reindex 保持摘要两端一致性」Requirement（重建索引不得清空 ES `doc_summary`）。

## Impact

- **代码**：`services/rag-service/app/`（api/deps.py、api/documents.py、api/search.py、api/health.py、main.py、services/ingest_service.py、services/search_service.py、services/asr_client.py、services/asr_providers/*.py、vectorstore/es_client.py、repositories/doc_repo.py、models/search.py 注释）；`common/config/settings.py`；`migrations/`（如有 schema 变更则随附，本变更预计无 DDL 变更）。
- **配置**：`common/config/settings.py` 新增 `KB_MAX_FILE_SIZE`、`KB_PARSE_TIMEOUT`；Dockerfile ENV 与 `.env.example` 同步。
- **文档**：`requirements_document/step2_需求PRD/DD-03-RAG-MULTIMODAL-KB.md`（§3/§5.1/§5.2/§6.4/§9/§10.3）；`openspec/specs/rag-service/` 下 3 个主 spec 经 sync 合入。
- **测试**：新增/修改单元测试覆盖 6 项 A 类与 8 项隐患修复；冒烟脚本（smoke_rag.sh / smoke_rag_v4.sh）保持全 PASS，涉及行为变化的步骤同步更新。
- **兼容性**：对外接口路径、响应包、错误码零变化；`top_k` 上界维持现状（20），重复删除语义维持现状（B0004）——本次仅文档对齐，不构成行为变更。

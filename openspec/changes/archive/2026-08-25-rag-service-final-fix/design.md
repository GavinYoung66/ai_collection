# rag-service-final-fix — 技术设计

## Context

基于 V1→V4 全量终局静态交叉评审（DD-03 × OpenSpec 主规范 × 最终源码）识别出 6 项 A 类阻断问题与 8 项隐患（详见 proposal.md — Why）。本设计给出 14 项的落地技术方案。当前约束：

- 代码基准：`services/rag-service/app/` 现有实现 + `common/config/settings.py`（全局配置中心）。
- 对外契约：接口路径、统一响应包 `{"code":"0",...}`、错误码（仅复用 A3001/A3003/B0001/B0004）零变化。
- 行为变更面：top_k 边界（维持 20）、重复删除（维持 B0004）经产品决策**仅文档对齐**，不改代码；其余修复以新增配置/内部健壮性为主，不改变既有默认行为。

## Goals / Non-Goals

**Goals:**

- 补齐生产就绪护栏：上传大小、解析超时、/health 真实探活。
- 修复 reindex 摘要两端一致性契约破坏。
- 提升删除/补索引健壮性：失败重试、任务持久化、告警可见。
- 消除 8 项静态隐患（临时文件泄漏、并发、N+1、非法 UUID 等）。
- 完成 DD-03 与代码/主 spec 的全量文档对齐（§3/§5.1/§5.2/§6.4/§9/§10.3）。

**Non-Goals:**

- 不引入任何新能力/新接口/新错误码（禁止新增错误码）。
- 不进行 V5 能力演进（chunk 级摘要、向量级摘要聚合检索等仍为 Backlog）。
- 不做流式上传重构、/health 双档探活等架构级优化（列入后续迭代）。
- 不修改 V1-V4 已归档模块与既有对外接口契约。

## Decisions

### D1 top_k 边界：仅文档对齐（不改代码）

- **决策**：维持代码 `models/search.py` 的 `top_k ∈ [1,20]`；修订 DD-03 §5.2 入参边界与 §10.3「永不收紧」措辞为 [1,20]；在 hybrid-search 主 spec 定稿边界（本变更 delta spec 已含）。
- **理由**：产品确认 20 够用；避免 422 行为变更。
- **备选**：放宽 `le=100`（尊重旧 DD 契约）→ 被产品否决。

### D2 上传大小上限

- **决策**：`common/config/settings.py` 新增 `KB_MAX_FILE_SIZE: int = 20 * 1024 * 1024`（注释对齐 DD §8）。`save_upload_file`（`api/deps.py`）增加校验：优先读取请求 `Content-Length` 头，> 上限直接抛 B0001；无头/不可信时先 `read()` 后按字节数校验（上限内才可读入，超限抛 B0001）。校验发生在任何解析/落库之前。
- **理由**：Content-Length 优先避免大文件入内存；read 后校验兜底 chunked 场景。
- **备选**：完全流式读取（`UploadFile.file` 分块）→ 改动面大，列入后续迭代。

### D3 解析超时

- **决策**：`settings.py` 新增 `KB_PARSE_TIMEOUT: float = 30.0`。在 `ingest_service.ingest` 与 reindex 调用 `parser.extract_text` 处统一包 `asyncio.wait_for(..., timeout=KB_PARSE_TIMEOUT)`；超时抛自定义 `ParseTimeoutError`，按既有解析失败路径处理（A3001 + status=failed；`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 走既有降级分支）。
- **理由**：单一超时点覆盖全部 parser（6 类），避免各 parser 各自实现。
- **备选**：各 parser 内部超时 → 重复实现且易漏，不采纳。

### D4 /health 依赖探活

- **决策**：统一到 `api/health.py` 单一实现（移除 `main.py` 根路径重复端点，仅保留 `/api/v1/health` 一处），对 PG（select 1）/ Milvus（collection 探测或 health 探测）/ Redis（ping）/ Embedding（探活调用）四项探测，每项 2s 超时；任一失败 → 返回非 healthy（响应结构以全局 step1/01 /health 规范为准：若规范规定 200+status 字段则按字段，若规定 503 则按规范）；探测异常不得 500。
- **理由**：双端点（`/health` 与 `/api/v1/health`）当前均为静态实现，双实现漂移风险高。
- **备选**：保留双端点各探活 → 重复实现，不采纳。

### D5 reindex 摘要一致性

- **决策**：`ingest_service` reindex 流程从 PG 回读 `doc.summary`，作为 `summary` 参数传入既有 ingest 入库路径；ingest 既有"先正文后摘要"写入顺序天然保证 ES `doc_summary` 与 PG 一致，reindex 后不再出现"PG 有摘要、索引无摘要"。
- **理由**：复用既有摘要写入链路，零新增调用；避免单独 `es.update_summary` 引入的时序问题。
- **备选**：reindex 完成后单独调 `es.update_summary` 回写 → 多一次跨服务调用且依赖更新语义，不采纳。

### D6 重复删除语义：仅文档对齐（不改代码）

- **决策**：维持代码（已 archived 返回 B0004）；修订 DD-03 §5.1 为「重复删除返回 B0004（非幂等）」；document-ingest 主 spec 以本变更 delta 显式固化该约定。
- **理由**：产品确认 B0004 语义可接受；spec/代码已一致，仅 DD 单侧过时。
- **备选**：代码补幂等 → 被产品否决。

### D7 临时文件清理

- **决策**：`save_upload_file` 保留 `delete=False`（避免 Windows 二次打开限制，项目跨平台开发），在 `finally` 块显式 `os.unlink(tmp_path)`；若调用方已消费文件后立即删除。
- **理由**：`delete=True` 的 `NamedTemporaryFile` 在 Windows 上无法被再次打开（解析器按路径读取），容器为 Linux 但需兼顾本地开发。
- **备选**：`delete=True` → 平台兼容风险，不采纳。

### D8 Whisper 模型加载并发

- **决策**：`whisper_provider.py` 模型懒加载处加模块级 `threading.Lock` + 双重检查，保证单进程内只加载一次。
- **理由**：`extract_text` 走 `asyncio.to_thread`，并发首请求会同时触发加载。
- **备选**：启动预热（应用启动时加载）→ 增加启动耗时，列入后续迭代。

### D9 ASR 熔断探测 Redis 异常容错

- **决策**：`asr_client.py` 与 `dashscope_provider.py` 的 `_is_degraded` 探测包 `try/except (RedisError, OSError)`，捕获后记录 warning（含 trace_id），按"未熔断"处理继续主流程。
- **理由**：与 hybrid-search 主 spec「熔断探测调用 MUST 纳入 try/except 容错」（V2 遗留缺陷 19.2 修复）保持同风格；避免 Redis 抖动连锁导致 ASR 链路异常。
- **备选**：上游捕获兜底 → 日志缺失且依赖上游纪律，不采纳。

### D10 删除失败重试与补偿

- **决策**：`ingest_service.delete_by_doc` 对 Milvus/ES 删除失败重试 2 次（退避 1s/3s，仅针对删除类异常）；重试仍失败 → 记录告警日志（doc_id、失败目标、异常），**不新增 DDL 列**，补偿由运维依据告警执行 `scripts/rag` 清理脚本；PG 软删除与业务返回不受影响。
- **理由**：删除路径追加重试成本低；加补偿列需 DDL 变更，超出本变更范围。
- **备选**：新增 `kb_documents` 补偿状态列 → DDL 变更，不采纳。

### D11 ES 降级补索引任务持久化

- **决策**：`_backfill_es_index` 启动时将待补 doc_id 写入 Redis 集合（`rag:backfill:pending`，DB2，TTL 30min 对齐重试窗口），任务完成/放弃后移除；`main.py` startup 扫描该集合恢复未完成任务并重新调度。
- **理由**：进程重启后 Redis 仍在，可恢复；避免新增 DB 表（数据库设计倾向加字段而非新表，且本场景 Redis 语义更贴切）。
- **备选**：新增 PG 表持久化 → DDL 变更，不采纳。

### D12 文档列表 N+1

- **决策**：`doc_repo.list_by_category` 改为一次分组聚合查询（`SELECT doc_id, COUNT(*) ... GROUP BY doc_id`）批量获取 chunk 计数，消除逐行 `get_chunks_count`。
- **理由**：分页最多 100 条时逐行查询代价高。
- **备选**：延迟加载 → 不改 N+1 本质，不采纳。

### D13 召回结果非法 UUID 容错

- **决策**：`search_service` 中 `chunk_ids → uuid.UUID` 转换处包 `try/except ValueError`，单条解析失败跳过并记录 warning，不中断整次检索；检索入口的 UUID 类入参校验由既有 Pydantic 约束覆盖（非法格式 422/B0001），本次仅补存储侧脏数据容错。
- **理由**：ES/Milvus 数据损坏场景下保证检索不 500。
- **备选**：严格失败（返回错误）→ 与"脏数据过滤"既有契约不符，不采纳。

### D14 全局客户端 shutdown 清理

- **决策**：`main.py` 注册 FastAPI shutdown 事件，显式关闭 embed/rerank/milvus/es/redis 客户端连接（幂等 close）。
- **理由**：当前单例客户端无关闭钩子，进程退出连接不释放。
- **备选**：依赖进程回收 → 长驻服务退出时资源不释放，不采纳。

## 配置项变更清单

| 配置项 | 默认值 | 位置 | 说明 |
|:--|:--|:--|:--|
| `KB_MAX_FILE_SIZE` | `20 * 1024 * 1024` | `common/config/settings.py` + Dockerfile ENV + `.env.example` | 上传上限（对齐 DD §8） |
| `KB_PARSE_TIMEOUT` | `30.0` | 同上 | 解析超时（对齐 DD §8） |

既有开关与配置（`KB_SUMMARY_*`、`KB_EMBED_DEGRADED_KEEP_TEXT`、`KB_OCR_ASR_DEGRADED_KEEP_TEXT`、`KB_ES_DEGRADED_KEEP_MILVUS`、`KB_QUERY_REWRITE_ENABLED` 等）默认值全部保持不变。

## 文档对齐清单（仅文档，零代码）

| 文档 | 修订点 |
|:--|:--|
| DD-03 §3 | 目录结构更新至 V4 快照（asr_providers/、ocr_client、asr_client、summary_service、conf/ 等）；health.py 职责改"依赖探活" |
| DD-03 §5.1 | 上传 file 标注 ≤20MB 超限 B0001；重复删除改为"返回 B0004（非幂等）" |
| DD-03 §5.2 | top_k 入参边界 [1,100] → [1,20] |
| DD-03 §6.4 | 闸门 1「标记 degraded」→「degraded 保持 false（非基础设施降级）」 |
| DD-03 §9 | 测试用例数 77 → 209 passed / 1 skipped |
| DD-03 §10.3 | 删除"top_k 边界永不收紧"，保留 category 四值不收紧 |

## Risks / Trade-offs

- **/health 探活误报**（依赖瞬时抖动导致 unhealthy）→ 探活带 2s 超时 + 单次重试；健康状态仅反映依赖可用性，不影响业务流量。
- **上传校验回退路径内存占用**（无 Content-Length 的 chunked 请求仍需先读入）→ 上限 20MB 内可接受；完全流式列入后续迭代。
- **删除重试增加耗时**（最坏约 4s）→ 仅失败场景触发，可接受。
- **补索引清单依赖 Redis**（ES 降级期间 Redis 若同时故障，任务仍可能丢失）→ 保持既有告警 + 运维脚本兜底，与原行为一致，不新增风险面。
- **D1/D6 文档对齐与最初产品预期差异** → 决策已记录于 proposal；后续如需 [1,100] 或幂等删除，需新开变更。

## Migration Plan

1. **部署**：先合入配置项（内置默认值，无需停机）→ 发布代码 → 跑冒烟。
2. **DD 文档修订**随发布说明一并提交，标注"文档对齐，非行为变更"。
3. **回滚**：全部修复为内部健壮性/新增校验，无数据迁移；`/health` 由静态转探活属行为变化，回滚仅需恢复旧代码版本。补索引持久化仅新增 Redis key，无历史数据兼容问题。

## Open Questions

- **/health 响应结构**：全局 step1/01 规范对 /health 的字段与 HTTP 状态约定（200+status vs 503）——实现前检索该文档定稿；设计已给出两种分支，不阻塞任务拆解。

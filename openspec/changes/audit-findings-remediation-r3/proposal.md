# Proposal

## Why

2026-11-05 完成的第三轮全面只读审计（`问题清单-2026-11-05-r3.md`）在 `common/` + 五服务 app 层 + `scripts/` 约 333 个生产文件 / 4.5 万行 Python 中新增 60 项发现（T87–T146：P0×3 / P1×10 / P2×23 / P3×24）。前两期整改的平台基座（`spawn_tracked`、`time_parse`、mask/SSRF 单源、`ON CONFLICT`、shutdown 清单、token type 隔离）经横切验证质量良好，但本轮暴露出一类新的系统性缺口：**鉴权机制的"末梢漏网"**——机制本身在 `auth.py`/`rbac.py` 中是完整的，但批次迭代拆出的路由组（qc demo/check、complaint mode/stats）与 data-platform 数据面整体未接线该机制，其中 qc demo/seed 端点会向未认证调用方签发合法 `qc_reviewer` token，构成可直接利用的特权升级路径（P0）。

同时存在三类同源问题必须在下一功能迭代前收敛：ASR 链路在事件循环内同步执行 ffprobe/ffmpeg（最长 630s）与逐采样裁剪；EventBus at-least-once 重投下 replay 明细双写（T97/T100）；五处进程级 append-only 列表无界增长（T102）与 complaint `_call_log` 驻留全文（T98）。这些问题无法由单点补丁根除，需要以"路由组覆盖完整性 + 事件循环纪律 + 幂等基线 + 有界内存"四条主线成组整改。

本变更承接的输入清单为 `问题清单-2026-11-05-r3.md`（下称"r3 清单"），**覆盖其中全部 60 项、不裁剪、不推迟**：清单第五节建议"批次 D 另立变更"以及"mcp-gateway 授权模型 / 多副本一致性另立专题"的点，本变更不改变其"先取证再定级"的实施方式，但仍将其作为独立批次纳入同一变更，避免跨变更拆分造成条目失联或验收口径分裂。

## What Changes

- **编号体系**：沿用前两期 `N.M [Txx]` 双编号惯例。`N.M` 为执行单元编号（全局唯一，本变更 `1.1`–`12.3` 共 60 个），`[Txx]` 为 r3 清单原始编号（T87–T146 追溯标签）；每个任务行另标注 r3 报告等级（P0/P1/P2/P3）。`design.md` D1 提供「60 项 → 12 批 → 32 条规格需求 / 13 项显式豁免」的完整映射表，`tasks.md` 逐项落地，三份文件可逐行对照。
- **批次重排**：r3 清单第五节只显式给出批次 A–D 的约 30 项归属（且未覆盖 T99、T101、T109、T113、T114、T117–T120、T123、T126、T128–T137、T140–T145 等条目），不可直接作为执行单元。本变更按「主题同批、严重程度降序、每批 3–7 项可独立验证交付」重排为 12 个批次；**不新增、不删除、不改写任何发现内容**，仅补齐清单未显式归批的条目。
- **鉴权末梢收口（批 1–2）**：qc demo/seed 与 check 三写端点、complaint mode/stats、data-platform 数据面、rag 文档读端点与敏感词库统一接入既有 `require_roles`/服务间凭证机制；qc demo 的 token 签发加显式 demo 开关与生产禁用闸门；complaint 双签 `signers` 改取已认证身份，禁止信任请求体自报。
- **事件循环与异步纪律（批 3）**：ASR `ffprobe`/`ffmpeg` 与 `_trim_silence` 迁出事件循环（`asyncio.to_thread`/`create_subprocess_exec`）；`TraceMiddleware` 由 `BaseHTTPMiddleware` 改纯 ASGI，消除 `/mcp` SSE 缓冲冲突；同步探活死代码同批修正。
- **正确性与幂等（批 4）**：complaint judge stub 加 `USE_MOCK_*` 生产闸门；qc `llm_reviewer` 补 logger；replay 在 at-least-once 重投下以 DONE 短路 + `ON CONFLICT DO NOTHING` 实现明细幂等，幂等检查改 `SET NX` 原子占坑；complaint `_call_log` 截断。
- **防护纵深（批 5–7）**：五处无界列表统一 `deque(maxlen=N)`/LRU；mcp-gateway `read_body` 加 1MB 上限与 disconnect 处理；三处 `@retry` 死代码修正；`time_parse`/session_id 白名单/pg_dsn 编码/限流键命名空间/top_k 收敛等单源漏网收口；LLM 双熔断注册表与 qc 手写熔断收敛；complaint mode Redis 装配、prompt 外发脱敏、人工复核非法输入 fail-safe、文档同名并发约束。
- **质量与性能（批 8–12）**：mcp-gateway 工具级 role ACL、`tests.*` 生产解耦、stats 数据源标记、rag 惰性单例加锁；AC 自动机/prompt loader 缓存、缓存 key 与 trace 一致性、dp 出站客户端复用、告警全量过滤、运行态键 TTL 与幂等键校验、统计查询类型容错、词库热更新与空库保护；P3 规范债与死代码按 13 项显式豁免口径批清。
- **规格化**：为路由鉴权覆盖、数据面服务间身份、工具级授权、JWT 签发/校验对称、事件循环非阻塞、常驻内存有界、事件重投明细幂等、惰性单例线程安全、单源收敛、运行期配置不可变、Prompt 外发脱敏、旁路埋点不阻断、工具参数边界、出站校验、请求体上限、重试分类、熔断恢复、超时口径单源、模式鉴权与一致性、统计数据源、Mock 门控、人工审阅 fail-safe、写入竞态、敏感词资源保护、缓存语义、限流键、告警全量过滤、运行态键 TTL、统计类型容错、词库热更新、出站复用共 32 条需求补充 16 份 capability 增量规格（均为 `ADDED Requirements`，不改动既有需求文本）。
- **显式豁免（13 项，不立规格）**：T118、T121、T123、T124、T125、T126、T135、T136、T138、T140、T141、T144、T146。理由逐项见 `design.md` D4：均为当前不可达的防御纵深、纯性能优化、死代码/类型注解/导入形态等规范债，无对外可观察行为变化，只进 `design.md` 与 `tasks.md`，不凑规格。

## Capabilities

### New Capabilities

（无。本变更不引入新 capability；全部需求增量落入既有已声明 capability，路径与 `openspec/specs/` 现有目录完全一致。）

### Modified Capabilities

以下既有 capability 的**需求集合**新增条目，既有需求文本不变，各对应一份 delta 规格。括号内为支撑的 r3 编号。

- `security/authn`: 新增「鉴权覆盖的路由组完整性」「数据面服务间身份与读权限一致」「工具级角色授权」「JWT 签发与校验参数对称」四条需求（T87/T88/T90/T110、T91/T92、T120、T145）。
- `platform/runtime-correctness`: 新增「事件循环非阻塞」「常驻进程内存有界」「事件重投下明细写入幂等」「惰性单例初始化线程安全」「单源收敛完整性」五条需求（T93/T94/T99/T142、T98/T102、T97/T100、T127、T105/T111/T115）。
- `platform/event-bus`: 新增「运行期总线配置不可变」一条需求（T134/T143）。
- `platform/observability`: 新增「Prompt 外发前脱敏」「旁路埋点故障不阻断主流程」两条需求（T113、T96）。
- `platform/mcp-gateway`: 新增「工具参数边界收敛」「出站校验全覆盖」「请求体读取上限与断连处理」三条需求（T108、T109、T103）。
- `platform/resilience-primitives`: 新增「重试分类契约」「熔断恢复与健康如实」「超时口径单源」三条需求（T104、T106/T107、T137）。
- `complaint-service/mode`: 新增「模式切换鉴权与审批身份来源」「模式状态多副本一致」两条需求（T89、T112）。
- `complaint-service/stats-service`: 新增「统计数据源标识」一条需求（T122）。
- `complaint-service/llm-layer`: 新增「Mock/Stub 路径的生产门控」一条需求（T95）。
- `complaint-service/graph-nodes`: 新增「人工审阅恢复的 fail-safe」一条需求（T114）。
- `rag-service/document-ingest`: 新增「文档写入竞态原子性」一条需求（T119）。
- `rag-service/sensitive-words-sync`: 新增「敏感词资源保护与字典单源」一条需求（T117/T139）。
- `rag-service/hybrid-search`: 新增「缓存键语义完整性与 trace 一致性」一条需求（T101/T128）。
- `qc-service/api-layer`: 新增「质检写端点限流身份可信」「告警查询全量过滤」「运行态键 TTL 与幂等键校验」「统计查询类型容错」四条需求（T116、T129、T130、T131）。
- `qc-service/engine`: 新增「词库热更新与空库保护」一条需求（T132）。
- `data-platform/min-loop`: 新增「出站客户端复用」一条需求（T133）。

## Impact

- **代码面**：`common/`（`trace/middleware.py` 纯 ASGI、`eventbus/rocketmq.py` 时间解析与运行期配置、`speech/*` 重试转译、`llm/provider.py` 熔断注册表、`resilience/retry.py`、`config/settings.py` DSN 编码、`security/rbac.py` 签发对称、`guardrails`/`prompt` 缓存、`utils/semaphore.py` 死代码）；`services/qc-service`（demo/check/llm_usage/alert/job_store/word_loader/replay/llm_reviewer/rag_client）；`services/complaint-service`（mode/stats/judge stub/human_review/subscriber/publisher/llm provider/graph nodes）；`services/rag-service`（documents/search/deps/asr/dashscope/asr_client/sensitive_words/milvus）；`services/mcp-gateway`（main/auth/asgi_util/audit/tools/rag_kb/qc_mcp_client/mcp_server/bridge_http）；`services/data-platform`（qc_source/qc_client/complaint_client/snapshot/publisher）。
- **对外行为变更（灰度须提前周知）**：①qc demo/check、complaint mode/stats、rag 文档读端点、rag 敏感词库、dp 数据面由匿名可达改为鉴权/内部隔离（批 1–2 生产立即生效）；②judge stub 在生产拒绝执行，误用不再产出假违规结论（批 4）；③replay 对已 DONE 会话短路、重复投递不再双写（批 4）；④模式切换 signers 不再接受请求体自报（批 7）；⑤`trace_id` 缓存命中语义、告警查询结果范围、统计接口对脏数据容错（原 500 场景转 200）、运行态键 TTL 与超长幂等键 400（批 9–10）。上述均无 API 路径/字段级破坏性变更，但权限与幂等语义收紧，需在灰度说明中逐项标注调用方。
- **验证与 CI**：逐项守护单测（鉴权矩阵、loop-lag 断言、重投幂等、上限/断连、无界列表、单源 AST 扫描）；T110/T120 与 T112 依赖待验证线索 V1（`GET /mcp` 实际行为）、V4（deploy nginx 是否清洗 `X-User-Id`）、V7/V11（多副本部署形态），MUST 先取证再定级实现，禁止按报告推断直接改协议行为。
- **依赖**：无新第三方依赖；全部使用标准库与项目既有零件（`defer`/`to_thread`/`deque`/`pg_insert`/既有熔断与限流零件）。
- **归档顺序约束**：`platform/runtime-correctness` 与 `security/authn` 在第二期变更 `audit-findings-remediation-r2` 中已有未归档的同名 delta。归档 MUST 先 `audit-findings-remediation-r2`（41/41 已完成）后本变更，避免同一 capability 双 `ADDED` 冲突；本变更新增需求标题已与 r2 需求标题逐条比对，无重名。

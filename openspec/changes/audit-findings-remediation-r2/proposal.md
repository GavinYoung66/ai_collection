# Proposal

## Why

第一期整改（`audit-findings-remediation`，T01–T45）于 2026-11-04 收尾后，2026-11-05 对仓库做了第二轮专项扫描（聚焦整改遗漏与整改引入的回归），产出 `问题清单-2026-11-05.md` 共 41 项新问题（T46–T86：高危 5 / 中危 20 / 低危 16）。其中存在三类「CI 门禁全绿但线上会出事」的缺陷，必须在下一功能迭代前收敛：

1. **T07/T10/T40 整改系统性遗漏**：`create_task` 强引用修复只落在 complaint-service 两个文件，rag-service 4 处调用点与 `_schedule_graph_resume` 均遗漏（文档可能永久卡 processing、人审后续流程静默丢失）；`aensure_persisted` 仍是 SELECT-then-INSERT；T40 静默异常有约 12 处漏网，其中两处正命中守卫测试禁止正则——该守卫测试疑未真实注册进 CI。
2. **整改引入的回归（休眠炸弹）**：T13 时区整改把 `updated_at` 全部变为 aware datetime，但清理任务仍以 `time.time()`（float）比较——`cleanup_stale`/`cleanup_archived_90d` 一旦接入 scheduler 即 100% 抛 TypeError，且缺时间戳的 archived 记录会被立即误删。
3. **安全红线补漏**：rag-service 用 JWT 做行级权限却未接入 T03 密钥 fail-fast（占位密钥 `change_me` 可伪造 admin token 绕过匿名白名单）；MCP 网关审计日志的嵌套 dict 参数整体绕过 T19 脱敏，PII 双路明文落盘。

原清单的扫描批次（B7–B13）按发现来源分组，B13 单批 16 项，不可作为执行单元。本 change 沿用第一期的固化方式：批次重排为 10 个均衡批次（每批 2–6 项），保留 `T46`–`T86` 追溯标签，新增层级编号 `N.M`，两份文件逐行对照。

## What Changes

- **编号体系**：沿用第一期执行级编号 `N.M`（批次号.序号，全局唯一，覆盖 `1.1`–`10.5` 共 41 个）；`T46`–`T86` 作为追溯标签写在任务行首（`N.M [Txx]`），并标注其在第二期报告中的原批次（如 `（报告 B7）`），与 `问题清单-2026-11-05.md` 一一对应。
- **批次重排**：报告 7 个扫描批次 → 10 个执行批次，按「主题同批、严重程度降序、每批 2–6 项可验证交付」重排；**不新增、不删除、不改写任何任务内容**。
- **系统性缺口以公共原语落地**（报告结论 1/3）：新增 `common.spawn_tracked(coro)` 统一原语（批次 1 一次性修复 T46/T47/T53 三族裸 `create_task`，防止四期再犯）；shutdown 释放闭环按各服务统一 shutdown 资源清单落地（批次 5，T50/T57–T61）；naive 时间解析收敛为单一事实源（批次 3/6，T51/T52/T64/T82）。
- **规格化**：为后台任务强引用、shutdown 释放闭环、时间与数值类型一致性、状态迁移原子性、事件循环非阻塞、凭据 fail-fast 完整性、令牌类型隔离、行级权限 fail-closed、限流身份来源可信、脱敏递归覆盖与单一事实源、静默异常零容忍收口、生产者初始化线程安全共 12 条需求补充 4 份 capability 增量规格（均为 `ADDED Requirements`，不改动既有需求文本）。
- **纯技术债项不立规格**：T73/T74/T75/T77/T78/T79/T83/T84/T85/T86 属内部加固、约定收敛或防御性缺陷（无当前触发路径或无对外可观察行为变化），只进 `design.md` 与 `tasks.md`，不凑规格。

## Capabilities

### New Capabilities

（无。本 change 不引入新 capability，全部需求增量落入第一期已建立或已声明的 capability。）

### Modified Capabilities

（以下既有 capability 的**需求集合**新增条目，既有需求文本不变，各对应一份 delta 规格。`platform/runtime-correctness` 由第一期 change 新建、尚未 archive——本 change 假定其需求集随第一期归档落地，归档顺序须先一期后二期。）

- `platform/runtime-correctness`: 新增「后台任务强引用与确定性清理」「shutdown 资源释放闭环」「时间与数值类型一致性」「状态迁移与写入原子性」「事件循环非阻塞」五条需求（T46/T47/T53、T50/T57–T61、T51/T52/T64/T81/T82/T66、T54/T63/T80、T65）。
- `security/authn`: 新增「凭据 fail-fast 接入完整性」「令牌类型隔离」「行级权限 fail-closed」「限流与审计身份来源可信」四条需求（T48、T69、T67、T68）。
- `platform/observability`: 新增「脱敏递归覆盖与单一事实源」「静默异常零容忍收口」两条需求（T49/T70/T76、T55/T56/T71/T72）。
- `platform/event-bus`: 新增「生产者初始化线程安全」一条需求（T62）。

## Impact

- **代码**：`common/`（新增 `spawn_tracked`、脱敏递归、时间解析单源、eventbus 生产者加锁、transport/client 释放入口）、`services/complaint-service`（清理任务类型回归、`_schedule_graph_resume`、`aensure_persisted`、静默异常批）、`services/rag-service`（ingest/backfill 任务引用、JWT fail-fast、行级权限 fail-closed、限流身份、whisper 线程池、shutdown 清单）、`services/qc-service`（DpClient 单例、复核条件更新、dedup_ac 时区、静默异常）、`services/data-platform`（lifespan 关停、金额精度消费端）、`services/mcp-gateway`（审计脱敏、bridge 连接池关闭）。
- **验证与 CI**：T56 要求核查 T40 守卫测试（`test_t40_silent_exception_logging` / `test_repo_wide_zero_silent_except_pass`）是否真实注册进 CI（pytest.ini / Makefile / workflow），若未执行则接入门禁——这是本 change 唯一的 CI 配置动作。
- **依赖**：无新第三方依赖（`spawn_tracked`、递归脱敏、时间解析单源均为标准库实现；shutdown 释放按 design D2.2 逐服务资源清单落地，不引入 Registry 抽象）。
- **兼容性**：无对外 API 契约变更。T69（refresh token 不能当 access token 用）属**部署期行为收紧**——若前端/调用方存在误用 refresh token 调业务接口的情况将被 401 拒绝，需在灰度说明中标注；T68（限流身份改验签身份/IP 派生）对正常用户无感知。
- **执行顺序约束**：批次 1（`spawn_tracked`）是批次内三项修复的公共前置；批次 3/6 的时间统一原语先于批 6 的 T64/T82 落地；本 change 全部任务不依赖第一期未完成项（第一期仓库侧已闭环）。

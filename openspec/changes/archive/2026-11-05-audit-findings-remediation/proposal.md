# Proposal

## Why

2026-11-02 对仓库做整体评估，产出 45 项问题（`问题清单.md`，原批次 B1–B6）。其中 4 项安全红线（真实 LangSmith Key 明文入库、认证全局默认关闭、JWT 默认密钥 `change_me`、种子脚本弱口令）与 6 项数据正确性缺陷（灰度分片用 `hash()` 跨进程不稳定、`_graph_inflight` check-then-act 竞态、质检回写事件先于落库即 `skip`、DLQ 无人消费且生产失败仍 ACK 等）属于「静态门禁全绿但线上会出事」的类型，必须在功能继续演进前收敛。

原清单的批次划分不可执行：B5 单批 17 项、B3 单批 8 项，而 B1 仅 4 项。批次内任务粒度混杂（安全、内存泄漏、架构重构同批），单批无法形成「一次可验证交付」，评审与回滚边界也不清晰。

本 change 把 45 项整改固化为 OpenSpec 规格与可执行任务清单：批次重排为 10 个均衡批次（每批 4–6 项），保留原 `T01`–`T45` 作为追溯标签，新增层级编号 `N.M` 作为执行单元编号，使两份文件可逐行对照。

## What Changes

- **编号体系**：新增执行级编号 `N.M`（批次号.序号，全局唯一，覆盖 `1.1`–`10.5` 共 45 个）；原 `T0x` 不作废，作为追溯标签写在每个任务行首（`N.M [T0x]`），保证 `问题清单.md` 与 `tasks.md` 一一对应。
- **批次重排**：6 批次 → 10 批次，仅调整批次边界、批次命名与配色；**不重排任务先后**（保持原清单「严重程度降序」的 T01→T45 顺序），**不新增、不删除、不改写任何任务内容**。
- **颜色标识**：每批次一个 emoji 色块 + 十六进制色值（红→灰按严重程度递降），用于文档与看板着色；纯文本标识，不引入 HTML/自定义语法，避免影响 Markdown 解析。
- **规格化**：为安全红线、默认凭据、事件可靠性、韧性零件、质量门禁、可观测性、出口 SSRF、运行时正确性补充 8 份 capability 增量规格（均为 `ADDED Requirements`，不改动既有需求文本）。
- **纯技术债项不立规格**：T14/T24/T25/T26/T29/T30/T31/T33/T35/T36/T37/T43/T44/T45 属内部重构、文档修正或部署编排，不产生对外可观察行为变化，只进 `design.md` 与 `tasks.md`，不凑规格。

## Capabilities

### New Capabilities

- `platform/runtime-correctness`: 平台级运行时正确性基线——并发互斥与幂等写、资源句柄生命周期、时间确定性（时区感知）、确定性灰度路由、以及「事件先于落库」的补偿语义。这些缺陷的共同特征是：不改变接口契约，但会改变同一输入下的可观察结果（偶发双跑、数据丢失、时区偏移）。

### Modified Capabilities

（以下既有 capability 的**需求集合**新增条目，既有需求文本不变，各对应一份 delta 规格。）

- `security/authn`: 新增「认证默认强制与敏感接口授权」「默认凭据与弱口令 fail-fast」两条需求（T02/T03/T04/T21）。
- `platform/secrets-sops`: 新增「明文密钥零容忍」「密钥占位值启动即失败」两条需求（T01/T41/T42）。
- `platform/event-bus`: 新增「死信消费与不丢语义」「订阅循环韧性」「事件白名单配置化与消费组收敛」「异步发布任务强引用」四条需求（T09/T12/T34/T07）。
- `platform/resilience-primitives`: 新增「熔断成功语义」「重试与熔断实现单源」两条需求（T18/T27/T28）。
- `platform/ci-matrix`: 新增「静态检查阻断与依赖漏洞扫描」一条需求（T20/T32/T38）。
- `platform/observability`: 新增「日志脱敏覆盖面与异常可观测」一条需求（T19/T40）。
- `platform/mcp-gateway`: 新增「出口 URL 白名单与 SSRF 防护」一条需求（T22）。

## Impact

- **安全**：4 项红线（T01–T04）必须在进入下一功能迭代前闭环；其中 T01 需在平台侧轮换/吊销已泄漏的 LangSmith Key，属仓库外动作，无法由代码提交闭环。
- **代码**：`common/`（config、eventbus、resilience、llm、observability、utils、security）、`services/complaint-service`、`services/qc-service`、`services/rag-service`、`services/mcp-gateway`、`data-platform` 均有改动点。
- **依赖**：T20 会升级 aiohttp / python-multipart / python-jose / cryptography，并新增 `pip-audit` 门禁，需与根 `requirements.in`/`requirements.lock` 的单一全局依赖源约束保持一致（不得新增服务级旁路清单）。
- **CI**：T32 要求 ruff/mypy 由 advisory 升级为阻断（涉及 `.github/workflows/ci.yml` 与根 `pyproject.toml`）；T20 新增依赖漏洞扫描 job/step。
- **部署**：T36 引入 `docker-compose.prod.yml` 与 RocketMQ 持久化卷、端口收敛；T24 用 compose `profiles` 收敛 3 个 health 空壳服务。
- **兼容性**：无对外 API 契约变更（不新增错误码）；T02/T21 若在生产开启认证，调用方需携带凭证，属**部署期行为收紧**，需在灰度说明中标注。T35 网关工具响应新增 `{"shape","payload"}` 形状元数据（经 structuredContent 返回，外部 MCP 调用方可见），同属**部署期行为收紧**，需在灰度说明中通知调用方（S-001 修订，2026-11-03 批次 8 评审裁决）。
- **文档**：T26 修正 README 架构图对「大模型网关」的表述（进程内编排器，非独立进程）。

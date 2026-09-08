## MODIFIED Requirements

### Requirement: 模型路由与四级熔断降级

系统 SHALL 在 `app/llm/router.py` 提供 `ModelRouter`，按 Agent 角色路由模型，并在 `app/llm/model_router.yaml` 维护路由配置，**在批次3 已有路由基础上增量增强**，禁止推倒重写导致批次3 路由回归：

**批次3 已有（保留）**：
- agent_role → primary_model + fallback_chain 路由
- 调度 Agent：不调用 LLM
- 反思 / 事实 / 用户诉求：轻量模型（qwen-turbo）
- 合规判定 / 综合判责：推理模型（deepseek-r1）
- 产出物生成：高阶模型（qwen-plus）
- 四级降级链：`deepseek-r1`(主) → `qwen-plus`(一级备) → `qwen-turbo`(二级备) → `RuleBasedScriptEngine`(兜底) → 全量熔断
- 模型名不硬编码在业务代码，走 `model_router.yaml` 配置
- deepseek-r1 熔断条件：连续 3 次失败或 P95 > 15s；熔断恢复后 1 分钟观察期
- 降级期间合规/判责 Agent 输出标记 `model_degraded=true` + `requires_human_review=true`

**批次4 增量增强**：
- 多环境差异：支持 dev/pre/prod 模型名与 API 配置差异（顶层 `environments` 分组，**配置层预留**）
- qwen-plus 熔断阈值：P95 > 800ms 触发降级（批次3 仅 deepseek-r1 有阈值）
- DeepSeek 差异配置：API base_url、`reasoning_content` 字段解析、reasoning token 单独计费
- 千问差异配置：DashScope 兼容模式 base_url

`ModelRouter.route(agent_role)` 行为在批次3 基础上不变（合规→deepseek-r1 等）。本批**默认不要求**修改 `router.py` 以读取 `environments`；增强项以 yaml 配置落地为主，避免路由回归。

#### Scenario: 按角色路由模型

- **WHEN** `ModelRouter.route("compliance")`
- **THEN** 返回推理模型名（deepseek-r1）

#### Scenario: 批次3 路由行为不变

- **WHEN** `ModelRouter.route("compliance")` / `route("reflection")` / `route("generate_package")`
- **THEN** 仍分别返回 deepseek-r1 / qwen-turbo / qwen-plus（批次3 行为不回归）

#### Scenario: qwen-plus 熔断阈值已配置

- **WHEN** 读取 model_router.yaml 中 qwen-plus 的 breaker 配置
- **THEN** 含 P95 阈值 800ms（或等价字段）

#### Scenario: 多环境配置存在

- **WHEN** 检查 model_router.yaml 顶层
- **THEN** 含 dev/pre/prod 环境差异配置（模型名或 API base 至少一项区分）；不要求本批 `route()` 已按环境切换

#### Scenario: 四级降级链

- **WHEN** 主模型连续失败达到熔断条件
- **THEN** 依次降级到一级备、二级备、规则兜底引擎；全部失败则全量熔断

#### Scenario: 降级标记

- **WHEN** 合规/判责 Agent 在降级路径下产出
- **THEN** 输出标记 `model_degraded=true` 且 `requires_human_review=true`
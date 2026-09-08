# complaint-service/llm-layer

## Purpose

为 complaint-service 提供 LLM 能力层：结构化输出 Provider、按 Agent 角色的模型路由与四级熔断降级、规则兜底引擎、熔断 Redis 标识，保障 LangGraph Agent 节点可稳定获取推理结果并在 LLM 全量故障时降级。

## Requirements

### Requirement: 投诉研判专用 LLM Provider

系统 SHALL 在 `app/llm/provider.py` 提供 `ComplaintLLMProvider`，核心方法 `async invoke(prompt: str, schema: type[BaseModel], *, trace_id: str, session_id: str) -> BaseModel`：
- 底层外部大模型 HTTP 走 `common.llm.transport`（`post_chat_completions`）；真路径经 `RealLLMTransport` + `ModelRouter` 编排。兼容入口 `common.llm.provider.chat` / `chat_json` 亦须经同一 transport，并复用 `common.llm.circuit_breaker`；禁止业务节点散落裸调供应商 SDK/HTTP
- Pydantic Schema 校验失败重试最多 3 次
- 每次调用累计 `total_tokens_consumed`，超 `max_token_threshold` 抛异常
- 强制 `response_format=json_object`；`temperature=0.1` / `top_p=0.9` / `seed=42`（可配置但默认硬约束）
- CoT 字段约定：思维链明文以 AES-256-GCM 加密写入 `cot_encrypted` 字段；本批密钥用配置 stub（`LLM_COT_KEY`），不接 KMS，但接口与字段名定死
- 调用审计：本批提供可测试的审计缓冲（内存 call_log / 占位 `llm_call_log_id`）；`llm_call_logs` 表已存在但无 ORM，**不强制**本批新建 ORM；可选 raw insert；写入 `complaint_agent_logs.llm_call_log_ids` 的完整链路可与后续节点批次衔接

#### Scenario: invoke 返回结构化 Pydantic 输出

- **WHEN** `ComplaintLLMProvider.invoke(prompt, LocalSchema, trace_id="t1", session_id="s1")` 在 Mock Provider 路径下执行（`LocalSchema` 为本批测试用最小 Pydantic 模型，**不**依赖批次5 的 `VerdictDraft` / `app/models/state.py`）
- **THEN** 返回该 Schema 实例，字段符合 schema

#### Scenario: Schema 校验失败重试

- **WHEN** LLM 返回的 JSON 不符合 schema
- **THEN** 自动重试最多 3 次；3 次仍失败则抛异常

#### Scenario: 超 max_token_threshold 抛错

- **WHEN** 累计 Token 超过 `max_token_threshold`
- **THEN** 抛出异常，由调用方决定降级

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

### Requirement: 规则兜底引擎

系统 SHALL 在 `app/llm/fallback.py` 提供 `ComplaintRuleEngine`，复用 `common.llm.rule_engine.RuleBasedScriptEngine` 结构，提供 6 个 generate 方法：
1. `generate_reflection_report` — 证据数量阈值校验
2. `generate_verdict_draft` — 质检告警存在性判断，confidence=0.5，verdict 枚举四值（agent_violation / user_misunderstanding / compliant_but_poor_experience / pending，不含 normal）
3. `generate_judgment_package` — 最小可用数据包
4. `generate_compliance_opinion` — 不引用法规
5. `generate_user_demand` — 关键词匹配
6. `generate_fact_timeline` — 时间戳排序

所有输出 SHALL 带 `[规则兜底]` 前缀。

#### Scenario: 6 个 generate 方法可用

- **WHEN** 调用 `ComplaintRuleEngine` 的任意 generate_* 方法
- **THEN** 返回结构化对象，输出带 `[规则兜底]` 前缀

#### Scenario: verdict 四值无 normal

- **WHEN** 调用 `generate_verdict_draft`
- **THEN** verdict 字段值属于四值枚举，不含 normal

### Requirement: LLM 熔断状态管理

系统 SHALL 在 `app/llm/breaker.py` 提供 `LLMBreaker`，复用 `common.llm.circuit_breaker`（pybreaker 实例）：
- 熔断时写 Redis `csmp:llm_degraded=1`（复用批次2 `app/utils/redis_keys.LLM_DEGRADED_KEY`，TTL 由 `TTL_LLM_DEGRADED=None` 定义）
- 恢复时删除该 Key
- 提供 `is_degraded() -> bool` 供调度 Agent 查询

#### Scenario: 熔断写 Redis 标识

- **WHEN** 熔断器打开
- **THEN** Redis Key `csmp:llm_degraded` 被设置为 "1"

#### Scenario: 恢复删除标识

- **WHEN** 熔断器关闭（恢复）
- **THEN** Redis Key `csmp:llm_degraded` 被删除

#### Scenario: is_degraded 可查询

- **WHEN** 调度 Agent 调用 `LLMBreaker.is_degraded()`
- **THEN** 返回当前熔断状态 bool

### Requirement: V2 关闭 Mock LLM 时的真实调用路径

系统 SHALL 在 `USE_MOCK_LLM=false` 时使用 `ComplaintLLMProvider` 的真实调用路径（复用 common LLM 与 `model_router.yaml` 角色路由），MUST NOT 默认委派给 `MockLLMProvider`。

#### Scenario: 配置关闭 Mock 后走真路径

- **WHEN** 环境为 `USE_MOCK_LLM=false` 且 Provider 工厂/装配被触发
- **THEN** 研判链路使用真实 Provider，而非 MockLLMProvider

#### Scenario: 路由仍由 model_router 决定

- **WHEN** 真路径下按 agent 角色发起调用
- **THEN** 所选模型名仍来自 `model_router.yaml`（或等价配置），不得在业务节点硬编码模型名

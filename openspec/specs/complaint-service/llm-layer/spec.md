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

系统 SHALL 由 `common.llm.reliability.invoke_llm` 统一承担 LLM 熔断与降级状态，取代批次3 的 pybreaker 单实例方案：

- 熔断主体为 common 进程内 per-model 熔断器（`common.resilience.breaker.BreakerRegistry`，key 为 `csmp:llm_degraded:{model}`，阈值/恢复窗口由 common 配置）；
- 主模型调用失败（超时或业务错重试耗尽）时 SHALL 经 `mark_degraded_flag` 写 Redis **按模型隔离**的降级标志 `csmp:llm_degraded:{model}` = "1"，TTL 60s，到期自动恢复（无需显式 DEL）；
- 系统 MUST NOT 再写入或查询全局单键 `csmp:llm_degraded`（批次3 的永不过期键废弃）；
- `app/llm/breaker.py` 的 pybreaker `LLMBreaker` SHALL 删除，调度 Agent MUST NOT 依赖全局 `is_degraded()` 做全量转人工；降级判定下沉到节点级（规则草稿 + `pending_review`，见 graph-nodes 能力）。

#### Scenario: 熔断写 Redis 标识

- **WHEN** 主模型调用失败（超时或业务错重试耗尽）
- **THEN** Redis 键 `csmp:llm_degraded:{model}` 被 SETEX 为 "1"（TTL 60s，按模型隔离）；全局键 `csmp:llm_degraded` MUST NOT 被写入

#### Scenario: 恢复删除标识

- **WHEN** per-model 标志置位后 60s 内无新的失败
- **THEN** 该键 TTL 到期自动过期，对应模型调用自动恢复，无需显式 DEL；全局键因已废弃不再存在删除路径

#### Scenario: is_degraded 可查询

- **WHEN** 调度或节点链路需要降级判定
- **THEN** 系统中不存在全局 `LLMBreaker.is_degraded()` 查询点（该类已删除）；`invoke_llm` 经 per-model 熔断器 `before_call()` 隐式判定，open 时抛 `CircuitBreakerOpenError`，节点据此走规则草稿 + `pending_review`

#### Scenario: 旧 pybreaker LLMBreaker 已移除

- **WHEN** 检查 `app/llm/` 包及其 import
- **THEN** 不存在 `app/llm/breaker.py` 与 `LLMBreaker` 的任何引用，也不存在全局 `csmp:llm_degraded` 的写入/查询路径；`fallback_chain` 不会被任何全局标志短路

### Requirement: V2 关闭 Mock LLM 时的真实调用路径

系统 SHALL 在 `USE_MOCK_LLM=false` 时使用 `ComplaintLLMProvider` 的真实调用路径（复用 common LLM 与 `model_router.yaml` 角色路由），MUST NOT 默认委派给 `MockLLMProvider`。

#### Scenario: 配置关闭 Mock 后走真路径

- **WHEN** 环境为 `USE_MOCK_LLM=false` 且 Provider 工厂/装配被触发
- **THEN** 研判链路使用真实 Provider，而非 MockLLMProvider

#### Scenario: 路由仍由 model_router 决定

- **WHEN** 真路径下按 agent 角色发起调用
- **THEN** 所选模型名仍来自 `model_router.yaml`（或等价配置），不得在业务节点硬编码模型名

### Requirement: Provider 走 invoke_llm 并遍历 fallback_chain

`ComplaintLLMProvider.invoke` SHALL 委托给 `common.llm.reliability.invoke_llm(model, messages, fallback_chain=..., timeout=..., breaker_key=...)`，不再只打主模型。`fallback_chain` SHALL 从配置读取为有序列表（主模型 → 备用 → 更弱模型，如 `qwen-plus → qwen-turbo → deepseek-r1`）。当主模型失败（超时、熔断 open、业务错重试耗尽）时，provider SHALL 继续走链中下一个模型，而不是直接判失败。

#### Scenario: 主模型不可用触发 fallback

- **WHEN** `fallback_chain` 中的主模型不可用（抛 `TimeoutError` 或 `CircuitBreakerOpenError`）
- **THEN** `invoke_llm` 继续走链中下一个模型；若下游模型成功，调用返回该结果，不把主模型失败冒泡给调用方

#### Scenario: 整条 fallback_chain 耗尽后优雅降级

- **WHEN** `fallback_chain` 中每个模型都失败（超时或熔断 open）
- **THEN** `invoke_llm` 置降级 flag 并把控制权交回调用方，调用方 MUST 走节点级降级路径（规则草稿 + `pending_review`）；HTTP 响应为 200 + `task_status=pending_review`，不是 500

### Requirement: 按模型隔离的 LLM 熔断 key

LLM 熔断 key SHALL 命名空间化为 `csmp:llm_degraded:{model}`。一个模型的熔断器 open MUST NOT 阻断链中其他模型的调用，保证主模型熔断时 `fallback_chain` 仍能遍历到备用模型。

#### Scenario: 主模型熔断 open 不阻塞备用模型

- **WHEN** 主模型的熔断器在 key `csmp:llm_degraded:{primary}` 下 open
- **THEN** `invoke_llm` 跳过主模型，尝试备用模型，其熔断 key `csmp:llm_degraded:{backup}` 独立不受影响

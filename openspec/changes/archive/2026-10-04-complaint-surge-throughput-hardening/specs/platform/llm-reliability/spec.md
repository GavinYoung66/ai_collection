## ADDED Requirements

### Requirement: LLM 模型维限流阈值可配置且单一事实源

LLM 模型维限流阈值（每窗口请求数与窗口秒数）SHALL 由环境变量配置（`LLM_RATE_LIMIT` / `LLM_RATE_WINDOW`），并 SHALL 在公共可靠性层与投诉服务真实 transport 之间收敛为单一事实源，禁止两处重复硬编码导致口径漂移。未配置时 SHALL 沿用现有默认值（60 次 / 60 秒），默认配置下行为与改造前完全一致。限流 key 仍按 `{svc}:llm_ratelimit:{model}` 分模型独立计数。限流依赖的 Redis 不可用时 SHALL 维持现有静默放行语义，不因本次配置化改变故障方向。

#### Scenario: 默认配置行为不变

- **WHEN** 未设置 `LLM_RATE_LIMIT` / `LLM_RATE_WINDOW`
- **THEN** 限流按 60 次 / 60 秒 / 模型生效，与改造前一致，存量测试全部通过

#### Scenario: 调高阈值后按新值计数

- **WHEN** 设置 `LLM_RATE_LIMIT=600` 并发起超过旧阈值但低于新阈值的请求
- **THEN** 请求不被限流拒绝，公共层与投诉 transport 使用同一阈值

#### Scenario: 两处入口口径一致

- **WHEN** 经公共可靠性编排或投诉真实 transport 发起同模型调用
- **THEN** 二者读取同一配置事实源，不存在两套不同阈值

#### Scenario: Redis 故障仍静默放行

- **WHEN** 限流计数时 Redis 不可达
- **THEN** 请求被放行并记录 warn，不因配置化而改变为 fail-closed

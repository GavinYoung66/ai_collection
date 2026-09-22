## ADDED Requirements

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

## MODIFIED Requirements

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

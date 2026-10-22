## Purpose

定义 qc-service M1 LLM 语义复核：Prompt `qc_review`、结构化输出校验，以及熔断后仍能在 A2011 下给出 AC+规则分。

## Requirements

### Requirement: Prompt qc_review 约束

系统 SHALL 提供 `services/qc-service/app/prompt/templates/qc_review.yaml`，`version=1.0.0`，对齐 DD-02 §13.2。Prompt MUST 约束：只判坐席违规；用户投诉/自杀/停呼不是坐席违规；仅当坐席继续催收施压才出 R13；MUST NOT 因缺护栏日志或未检索知识库输出 R10/`unverified` 或 R14；证据 MUST 引用已有 `turn_id`；MUST NOT 捏造金额或规则。

#### Scenario: Prompt 禁止因缺日志输出 unverified

- **WHEN** 加载 `qc_review.yaml`
- **THEN** 约束包含：不得因缺护栏日志或未检索知识库输出 R10/unverified 或 R14

### Requirement: LLM 复核走 invoke_llm 与 fallback_chain

LLM 语义复核 MUST 经共享入口 `invoke_llm(model, messages, fallback_chain=..., timeout=..., transport=...)` 调用外部模型。MUST NOT 再对主模型单独套私有 `call_with_retry` + 全局熔断器。主模型 SHALL 为 `QC_LLM_MODEL`（默认 `qwen-plus`）；`fallback_chain` SHALL 从 `QC_LLM_FALLBACK_CHAIN` 读取（默认备用 `qwen-turbo`）。超时 MUST 为 `Timeout(total=QC_LLM_TIMEOUT)` 且默认 5s，超时尝试 MUST NOT 在同一模型上重试。业务错有界重试 MUST 仅由 `invoke_llm` 承担。主模型失败后 MUST 尝试链中下一模型；整链失败或熔断 open MUST 跳过 LLM 复核，仍出 AC+规则分。

#### Scenario: 主模型不可用走备用模型

- **WHEN** 主模型超时或业务错重试耗尽，且 fallback_chain 中备用模型可用
- **THEN** 复核使用备用模型结果继续管道，不因主模型失败返回 HTTP 500

#### Scenario: LLM 超时仍出 AC+规则分

- **WHEN** 主模型与 fallback_chain 全部超时或不可用
- **THEN** 同步路径 HTTP 200、`code=A2011`，data 仍含 AC 词库命中与规则分，不是 500，`qc_status` MUST NOT 仅因 LLM 失败变为 PARTIAL

### Requirement: LLM 熔断 key 按模型分片

LLM 熔断与降级标志 MUST 使用 `qc:llm_degraded:{model}`。一个模型熔断 open MUST NOT 阻断链中其他模型。系统 MUST NOT 再写入或查询全局单键 `qc:llm_degraded`。健康检查 `llm_degraded` 口径见 `qc-service/observability`：仅当配置链全部 open 时为 true，不是任一模型 open。

#### Scenario: 主模型熔断不阻断备用模型

- **WHEN** Redis/熔断器在 `qc:llm_degraded:{primary}` 下 open
- **THEN** 入口跳过主模型并尝试备用模型，其 key `qc:llm_degraded:{backup}` 独立不受影响

#### Scenario: 全局 qc:llm_degraded 不再使用

- **WHEN** LLM 复核失败触发熔断
- **THEN** 写入的是 `qc:llm_degraded:{model}`，全局键 `qc:llm_degraded` MUST NOT 被 SET

### Requirement: LLMRateLimitError 跳过 LLM 仍出分

当 `invoke_llm` 因模型维限流抛 `LLMRateLimitError` 时，同步路径 MUST 跳过 LLM 复核，MUST 返回 HTTP 200 且 `code=A2011`，data 仍含 AC+规则分。MUST NOT 返回 HTTP 500。MUST NOT 仅因此将 `qc_status` 置为 PARTIAL。本变更 MUST NOT 修改 common 的 60/min 阈值。

#### Scenario: 模型维限流不是 500

- **WHEN** 主模型窗口内超过 invoke_llm 模型维限流
- **THEN** 响应 HTTP 200、`code=A2011`，data 含 AC 词库命中与规则分，不是 500

### Requirement: invoke_llm 注入 json_object transport

调用 `invoke_llm` 时 MUST 注入 QC 自有 transport，签名 `(model, messages, timeout_seconds) -> str`。transport MUST 保留现网 `response_format=json_object`，并使用 invoke_llm 传入的目标 `model`（含 fallback 换模）。MUST NOT 使用会丢掉 `json_object` 的默认 DashScope transport。

#### Scenario: fallback 换模型仍要求 JSON 对象

- **WHEN** 主模型失败后改打备用模型
- **THEN** 对备用模型的 HTTP 调用仍带 `response_format=json_object`，输出可按既有 Pydantic schema 解析

### Requirement: LLMReviewer 结构化输出且无置信度门控

LLMReviewer SHALL 经 `invoke_llm` 调用（`Timeout(total=QC_LLM_TIMEOUT)`，默认 5s）。超时 MUST NOT 在同一模型上重试；业务错有界重试 MUST 仅由 `invoke_llm` 承担；MUST NOT 在入口外再套 `call_with_retry`。JSON SHALL 解析为含 `level/rule_id/redline_tag/evidence/reason/confidence` 的 Pydantic schema。证据 `turn_id` 不在入站 turns 中的 hit SHALL 丢弃。`reason` MUST NOT 含捏造金额。用户投诉/自杀 MUST NOT 标为坐席违规，除非后续坐席施压构成 R13。系统 MUST NOT 施加 confidence<0.7 门控。调用 SHALL 落 `llm_call_logs`，带 `qc_review_exemption`。

#### Scenario: 未知 turn_id 丢弃

- **WHEN** LLM 返回的证据引用了会话中不存在的 turn_id
- **THEN** 该 LLM hit 丢弃，其余 AC+规则打分继续

### Requirement: 熔断 A2011 用 Redis 键而非 qc.llm.degraded 频道

连续失败达阈值后熔断 SHALL OPEN：跳过该模型的 LLM 调用，仍出 AC+规则分，按模型 `SET qc:llm_degraded:{model}`（TTL 随共享零件，到期自动恢复），并发布 `system.alert.raised`，payload `{alert_id, level, event, service, description, error_code, trace_id, ts}`，其中 `event=qc:llm_degraded`、`error_code=A2011`、`service=qc-service`。同步路径碰上 OPEN SHALL HTTP 200 且 `code=A2011`，data 仍含 AC+规则分（DD-02；MUST NOT 对该码套用 step5/02 失败时 `data=null`）。系统 MUST NOT 发布频道 `qc.llm.degraded`。半开 SHALL 在恢复窗口后发生。系统 MUST NOT 使用全局键 `qc:llm_degraded` 作为熔断权威。

#### Scenario: 熔断打开仍出分

- **WHEN** replay 中连续三次 LLM 失败触发熔断
- **THEN** 响应 HTTP 200 `code=A2011`，Redis 键 `qc:llm_degraded:{model}` 已 SET，已发布 `system.alert.raised`，且无 `qc.llm.degraded` 频道消息，且无全局键 `qc:llm_degraded`

#### Scenario: 半开恢复清除键

- **WHEN** 恢复窗口后半开探测成功
- **THEN** 对应模型的 `qc:llm_degraded:{model}` 标志清除或过期，该模型 LLM 复核恢复；其他模型状态不因此被清空

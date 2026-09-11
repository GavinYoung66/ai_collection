## Purpose

定义 qc-service M1 LLM 语义复核：Prompt `qc_review_v1`、结构化输出校验，以及熔断后仍能在 A2011 下给出 AC+规则分。

## Requirements

### Requirement: Prompt qc_review_v1 约束

系统 SHALL 提供 `common/llm/prompts/qc_review.yml`，`version=qc_review_v1`，对齐 DD-02 §13.2。Prompt MUST 约束：只判坐席违规；用户投诉/自杀/停呼不是坐席违规；仅当坐席继续催收施压才出 R13；MUST NOT 因缺护栏日志或未检索知识库输出 R10/`unverified` 或 R14；证据 MUST 引用已有 `turn_id`；MUST NOT 捏造金额或规则。

#### Scenario: Prompt 禁止因缺日志输出 unverified

- **WHEN** 加载 `qc_review.yml`
- **THEN** 约束包含：不得因缺护栏日志或未检索知识库输出 R10/unverified 或 R14

### Requirement: LLMReviewer 结构化输出且无置信度门控

LLMReviewer SHALL 经 `common.llm` 调用（超时 5s，重试 3×）。JSON SHALL 解析为含 `level/rule_id/redline_tag/evidence/reason/confidence` 的 Pydantic schema。证据 `turn_id` 不在入站 turns 中的 hit SHALL 丢弃。`reason` MUST NOT 含捏造金额。用户投诉/自杀 MUST NOT 标为坐席违规，除非后续坐席施压构成 R13。系统 MUST NOT 施加 confidence<0.7 门控。调用 SHALL 落 `llm_call_logs`，带 `qc_review_exemption`。

#### Scenario: 未知 turn_id 丢弃

- **WHEN** LLM 返回的证据引用了会话中不存在的 turn_id
- **THEN** 该 LLM hit 丢弃，其余 AC+规则打分继续

### Requirement: 熔断 A2011 用 Redis 键而非 qc.llm.degraded 频道

连续 3 次超时或 HTTP 5xx 后熔断 SHALL OPEN：跳过 LLM，仍出 AC+规则分，`SET qc:llm_degraded`，并发布 `system.alert.raised`，payload `{alert_id, level, event, service, description, error_code, trace_id, ts}`，其中 `event=qc:llm_degraded`、`error_code=A2011`、`service=qc-service`。同步路径碰上 OPEN SHALL HTTP 200 且 `code=A2011`，data 仍含 AC+规则分（DD-02；MUST NOT 对该码套用 step5/02 失败时 `data=null`）。系统 MUST NOT 发布频道 `qc.llm.degraded`。半开 SHALL 在 60s 后发生。

#### Scenario: 熔断打开仍出分

- **WHEN** replay 中连续三次 LLM 失败触发熔断
- **THEN** 响应 HTTP 200 `code=A2011`，Redis 键 `qc:llm_degraded` 已 SET，已发布 `system.alert.raised`，且无 `qc.llm.degraded` 频道消息

#### Scenario: 半开恢复清除键

- **WHEN** 60s 后半开探测成功
- **THEN** Redis `qc:llm_degraded` 被删除，LLM 复核恢复

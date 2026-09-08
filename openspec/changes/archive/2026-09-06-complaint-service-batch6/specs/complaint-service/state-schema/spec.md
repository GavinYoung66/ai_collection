## MODIFIED Requirements

### Requirement: State Schema 模型定义

系统 SHALL 在 `app/models/state.py` 定义/增强以下模型（对齐 PRD 6.1），所有字段 MUST 含 `Field(...)` 描述：

**枚举**：
- `TaskStatus`（10 值）：pending / evidence_gathering / reflection_retry / judging / draft_generated / pending_review / pending_senior_review / resolved / closed / archived
- `VerdictType`（4 值，**不含 normal**）：agent_violation / user_misunderstanding / compliant_but_poor_experience / pending
- `ConfidenceLevel`（3 值）：high / medium / low
- `ReviewAction`（6 值）：approve / modify_verdict / modify_response / reject_rejudge / add_evidence / escalate

**本批增量**：
- `VerdictDraft`：**新增 `per_case_verdicts: dict[str, VerdictType]`**；`confidence < 0.7` 时 `conflict_highlight=True`
- `HumanReviewResult`：**新增 `review_action: ReviewAction`**（可 Optional）；可镜像展示 reject 信息
- `ComplaintSessionState`：**新增根字段 `reject_count: int = 0`**（权威驳回计数，供 `should_proceed_after_review` 读取；跨轮持久，不得仅存在于易被清空的嵌套对象）
- `ExceptionInfo`：支持 `error_type=llm_degraded`

核心标识字段 MUST 可在测试中显式构造；其余字段 SHOULD 提供合理默认值。

#### Scenario: 枚举值齐全且无 normal

- **WHEN** 检查各枚举 `.value`
- **THEN** TaskStatus 10 值；VerdictType 四值无 `normal`；ReviewAction 6 值

#### Scenario: VerdictDraft 含逐案判责

- **WHEN** 构造 VerdictDraft 并传入 per_case_verdicts
- **THEN** 值为 VerdictType 且不含 normal

#### Scenario: reject_count 在 State 根上持久

- **WHEN** human_review 多次 reject_rejudge
- **THEN** `ComplaintSessionState.reject_count` 累加，条件边读该字段

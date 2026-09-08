# complaint-service/state-schema

## Purpose

为 complaint-service LangGraph 编排提供统一 State Schema：对齐 PRD 6.1 的全部 Pydantic 模型（TaskStatus、VerdictType、ConfidenceLevel、证据/Agent 输出/异常/人工/会话状态），以及 `merge_case_evidence` 自定义 reducer 解决并行证据节点写入竞态（FATAL-3）。reducer 语义以 **PRD 6.1 字段级覆盖** 为准。

## Requirements

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

### Requirement: merge_case_evidence reducer 与 case_evidence 标注

系统 SHALL 提供 `merge_case_evidence(left, right) -> dict` 自定义 reducer，并将 `ComplaintSessionState.case_evidence` 字段标注为 `Annotated[dict[str, CaseEvidenceBundle], merge_case_evidence]`（FATAL-3 修复）。

reducer 语义（**对齐 PRD 6.1，禁止改成列表合并去重**）：
- 按 case_id 键合并两个 dict
- 同一 case_id：右侧 Bundle 中**有值**的字段整字段覆盖左侧同名字段（case_info / call_evidence / history_tickets / history_complaints / qc_alerts）
- 空 list / None 视为无值，不覆盖左侧
- 保证并行证据节点（evidence_case / evidence_call_qc）写入互补字段时无竞态丢失

#### Scenario: 并行写入无竞态（字段互补）

- **WHEN** 两节点分别写入 `case_evidence={"c1": CaseEvidenceBundle(case_info=...)}` 与 `case_evidence={"c1": CaseEvidenceBundle(call_evidence=[...])}`
- **THEN** reducer 合并后 `case_evidence["c1"]` 同时含 case_info 与 call_evidence，无字段丢失

#### Scenario: 同名字段右侧有值则整字段覆盖

- **WHEN** 左侧 `call_evidence=[A]`，右侧同 case_id `call_evidence=[B]`
- **THEN** 合并结果为 `[B]`（整字段替换，非 `[A,B]` 去重合并）

#### Scenario: 不同 case_id 各自保留

- **WHEN** 左侧 `{"c1": ...}`，右侧 `{"c2": ...}`
- **THEN** 合并结果含 c1 与 c2 两个键

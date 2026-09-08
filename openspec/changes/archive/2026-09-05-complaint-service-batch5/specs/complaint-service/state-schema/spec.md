## Purpose

为 complaint-service LangGraph 编排提供统一 State Schema：对齐 PRD 6.1 的全部 Pydantic 模型（TaskStatus、VerdictType、ConfidenceLevel、证据/Agent 输出/异常/人工/会话状态），以及 `merge_case_evidence` 自定义 reducer 解决并行证据节点写入竞态（FATAL-3）。reducer 语义以 **PRD 6.1 字段级覆盖** 为准。

## ADDED Requirements

### Requirement: State Schema 模型定义

系统 SHALL 在 `app/models/state.py` 定义以下 Pydantic 模型（对齐 PRD 6.1），所有字段 MUST 含 `Field(...)` 描述：

**枚举**：
- `TaskStatus`（10 值）：pending / evidence_gathering / reflection_retry / judging / draft_generated / pending_review / pending_senior_review / resolved / closed / archived
- `VerdictType`（4 值，**不含 normal**）：agent_violation / user_misunderstanding / compliant_but_poor_experience / pending
- `ConfidenceLevel`（3 值）：high / medium / low

**数据模型**：
- `EvidenceItem`：证据条目（含 `confidence_level`、`is_corrupted` 等，对齐批次2 / PRD）
- `CaseEvidenceBundle`：单案件证据包（case_info / history_tickets / history_complaints / call_evidence / qc_alerts）
- `ReflectionReport`：反思报告
- `FactTimeline`：事实时序
- `ComplianceOpinion`：合规意见（含 regulation_refs）
- `UserDemandAnalysis`：用户诉求分析（demand_type / emotion_level / secondary_complaint_risk）
- `VerdictDraft`：判责草稿（verdict: VerdictType）
- `JudgmentPackage`：研判数据包
- `ExceptionInfo`：异常信息
- `HumanReviewResult`：人工复核结果
- `ComplaintSessionState`：LangGraph 全局状态（含 case_ids / case_evidence / loop_count / task_status / 各 Agent 输出 / exception_info 等）

核心标识字段（session_id / trace_id / complaint_id / complaint_text 等）MUST 可在测试中显式构造；其余字段 SHOULD 提供合理默认值，便于节点冒烟。

#### Scenario: 枚举值齐全且无 normal

- **WHEN** 检查 `TaskStatus` / `VerdictType` / `ConfidenceLevel` 枚举成员的 **`.value`**
- **THEN** TaskStatus 含 10 值；VerdictType 含四值且不含 `normal`；ConfidenceLevel 含 high/medium/low

#### Scenario: 模型可实例化且字段有描述

- **WHEN** 对任一模型调用 `model_fields` 检查，并用测试辅助构造最小 ComplaintSessionState
- **THEN** 全部字段含 `Field` 描述；最小 State 可成功实例化

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

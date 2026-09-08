## Purpose

定义人工复核服务：6 种复核动作、reject_count 硬上限 2、驳回原因枚举、状态映射、agent_logs 记录。

## Requirements

### Requirement: 人工复核 6 动作

系统 SHALL 在 `app/service/review_service.py` 支持 6 种复核动作：
approve / modify_verdict / modify_response / reject_rejudge / add_evidence / escalate

#### Scenario: approve 动作

- **WHEN** 提交 approve
- **THEN** 工单状态变为 resolved

#### Scenario: reject_rejudge 动作

- **WHEN** 提交 reject_rejudge
- **THEN** 工单状态变为 evidence_gathering，reject_count +1

### Requirement: reject_count 硬上限

系统 SHALL 对 reject_count 实施硬上限 2：第 3 次驳回强制 escalate 至 pending_senior_review，不再允许 reject_rejudge。

#### Scenario: 超限强制 escalate

- **WHEN** reject_count 已达 2 后再次提交 reject_rejudge
- **THEN** 动作被强制转为 escalate，状态 pending_senior_review

### Requirement: 驳回原因枚举

系统 SHALL 要求 reject_rejudge / modify_* 携带驳回原因，枚举值：
insufficient_evidence / wrong_verdict / bad_response / other

#### Scenario: 驳回原因必填

- **WHEN** 提交 reject_rejudge 未带原因
- **THEN** 拒绝，错误码 A5002（或等价校验 fail；不新增错误码）

### Requirement: 状态流转映射

系统 SHALL 按以下映射流转状态：
- approve → resolved
- reject_rejudge / add_evidence → evidence_gathering
- escalate → pending_senior_review
- modify_verdict / modify_response → judging（重新生成）

#### Scenario: escalate 状态映射

- **WHEN** 提交 escalate
- **THEN** 状态变为 pending_senior_review

### Requirement: 写 agent_logs 与 verdict 四值

系统 SHALL 对每种复核动作写入 complaint_agent_logs；verdict 枚举必须为四值（agent_violation / user_misunderstanding / compliant_but_poor_experience / pending），禁止 normal。

#### Scenario: verdict 非法值拒绝

- **WHEN** modify_verdict 提交 verdict=normal
- **THEN** 拒绝，错误码 A5002（不新增错误码）

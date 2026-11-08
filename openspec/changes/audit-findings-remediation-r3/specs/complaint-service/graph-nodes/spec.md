# Spec Delta

## ADDED Requirements

### Requirement: 人工审阅恢复的 fail-safe 判定

人工审阅节点从持久化状态恢复时，恢复值 MUST 先经严格校验。非法、越界或恶意构造的值 MUST 落入待人工处理状态并记录异常信息，MUST NOT 被解释为 approve，MUST NOT 触发工单自动结案。

#### Scenario: 非法恢复值不自动结案

- **WHEN** 人工审阅节点恢复到的动作值为未知字符串或非法枚举
- **THEN** 工单保持待复核状态，`exception_info` 记录非法值，不转入 resolved

#### Scenario: 合法恢复值正常推进

- **WHEN** 恢复值为受支持的 approve/reject 动作
- **THEN** 流程按该动作正常推进，并保留审计记录

#### Scenario: 缺失恢复值保守处理

- **WHEN** 恢复状态缺失或为空
- **THEN** 不默认 approve，保持待复核并告警

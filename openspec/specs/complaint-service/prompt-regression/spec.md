## Purpose

Prompt 回归：以 golden set 校验 verdict 一致率，verdict 枚举仅允许四值，用于防止提示词变更引入判定漂移。

## Requirements

### Requirement: golden set 与回归测试

系统 SHALL 提供 `tests/regression/golden_set/` 与 `test_prompt_regression.py`。

预期 verdict **仅允许**：
- `agent_violation`
- `user_misunderstanding`
- `compliant_but_poor_experience`
- `pending`

禁止使用孤立值 `compliant`。

#### Scenario: 一致率门槛

- **WHEN** 执行回归
- **THEN** verdict 一致率 ≥95%，否则失败

#### Scenario: 样本不足 50

- **WHEN** 样本 <50
- **THEN** 以现有样本运行；design 已写 50+ 渐进策略

### Requirement: 可复现

系统 SHALL 使用固定 seed 与 Mock LLM（或可切换真 LLM），保证相同输入产出相同 verdict，回归结果可复现。

#### Scenario: 可复现

- **WHEN** 同一 golden set 连续运行两次
- **THEN** verdict 结果一致

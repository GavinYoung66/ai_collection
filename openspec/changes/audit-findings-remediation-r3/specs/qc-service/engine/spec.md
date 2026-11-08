# Spec Delta

## ADDED Requirements

### Requirement: 词库热更新与空库保护

词库同步调度配置 MUST 真实参与调度，MUST NOT 被读取后闲置。空词库同步 MUST NOT 静默清空 AC 自动机的检测能力；MUST 保留上一有效版本并输出告警，或显式拒绝替换。

#### Scenario: 空词库不清空检测能力

- **WHEN** 一次同步返回空列表并触发词库重载
- **THEN** 保留上一有效词库继续检测，记录 warning，不把 AC 自动机置空

#### Scenario: 定时配置按预期触发

- **WHEN** 配置了小时级同步 cron
- **THEN** 同步任务按配置周期实际执行，可在日志中观察到调度记录

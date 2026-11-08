# Spec Delta

## ADDED Requirements

### Requirement: 统计数据源标识

统计与一致性接口的响应 MUST 标识数据来源。使用硬编码演示值实现时 MUST 返回 `data_source="demo_stub"` 标记（或返回 501 未实现），MUST NOT 以真实指标形态输出演示数据。接入真实数据源后 MUST 返回对应的来源标识。

#### Scenario: 演示实现被明确标记

- **WHEN** 调用一致性接口且当前实现为硬编码演示数据
- **THEN** 响应包含 `data_source="demo_stub"`，监控与报表消费方可据此区分

#### Scenario: 真实数据源标识

- **WHEN** 统计接口接入数据库或缓存中的真实明细
- **THEN** 响应 `data_source` 为对应真实来源，不再出现演示常量

#### Scenario: 未实现时显式拒绝

- **WHEN** 统计能力尚未实现且未启用演示标记
- **THEN** 接口返回 501，不返回看似真实的假指标

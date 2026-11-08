# Spec Delta

## ADDED Requirements

### Requirement: Mock/Stub 路径的生产门控

研判 stub MUST 仅在 `USE_MOCK_MCP` 与 `USE_MOCK_LLM` 等显式开关开启时执行。生产模式 MUST 拒绝 stub 执行或改走真实研判链路，MUST NOT 伪造违规结论并发布真实业务事件。`run_graph` 在生产 MUST 默认为真，MUST NOT 因请求体缺省而落到 stub 路径。

#### Scenario: 生产环境请求缺省 body 不产生假结论

- **WHEN** 生产环境收到一条不带 body 或 `run_graph` 缺省的研判请求
- **THEN** 不执行 stub，不产出"坐席违规"类固定结论，不发布 `complaint.judged`/`case.risk.upgraded`

#### Scenario: 演示开关开启时 stub 可用

- **WHEN** 显式开启 Mock 开关并调用研判
- **THEN** stub 按演示语义返回结果，事件发布受演示门控约束

#### Scenario: 生产默认走真实图

- **WHEN** 生产环境正常发起研判且未显式关闭图执行
- **THEN** 默认执行真实图链路，stub 不可达

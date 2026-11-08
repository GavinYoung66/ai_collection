# Spec Delta

## ADDED Requirements

### Requirement: Prompt 外发前脱敏

所有发往第三方 LLM 的 prompt MUST 在 Provider 入口完成单点脱敏（手机号、证件号、银行卡等 PII），MUST NOT 依赖各业务节点各自处理。脱敏 MUST 复用 common 安全模块的单一事实源实现。

#### Scenario: 投诉自述中的 PII 不落第三方

- **WHEN** 投诉文本包含手机号、身份证号并发往第三方 LLM
- **THEN** 外发 payload 中对应字段已被掩码，不含明文

#### Scenario: 所有 LLM 节点经同一入口

- **WHEN** 事实抽取、合规判定、结论、反思等节点分别构造 prompt 调用 LLM
- **THEN** 每个节点的外发 payload 均经过同一脱敏入口

### Requirement: 旁路埋点故障不阻断主流程

埋点、审计与旁路持久化失败 MUST 被捕获并以结构化日志留痕。旁路代码 MUST NOT 因未定义符号、类型错误或异常传播中断主流程，MUST NOT 改变主流程的成功/失败结论。

#### Scenario: 数据库闪断时质检结论不受影响

- **WHEN** LLM 调用日志的独立短事务因数据库闪断失败
- **THEN** 主质检流程继续完成并返回正常结果，日志记录包含失败原因与堆栈

#### Scenario: 旁路分支不抛出未定义符号错误

- **WHEN** 旁路异常处理分支被执行
- **THEN** 不出现 NameError/AttributeError 等代码级异常传播到主流程

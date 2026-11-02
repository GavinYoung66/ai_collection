## ADDED Requirements

### Requirement: 日志脱敏覆盖面与异常可观测

日志脱敏 MUST 覆盖全部 PII 类型（至少手机号、身份证号、银行卡号），且 MUST 复用统一掩码实现，MUST NOT 在各服务维护各自的脱敏正则。

捕获异常后 MUST NOT 静默丢弃（空 `pass`）。至少 MUST 记录 warning 级日志并附异常栈；业务允许忽略的异常 MUST 在代码处注明忽略理由。

#### Scenario: 身份证与银行卡不落明文

- **WHEN** 日志内容包含身份证号或银行卡号
- **THEN** 输出为掩码形式，明文不出现在日志文件

#### Scenario: 异常不被静默吞掉

- **WHEN** 核对或统计任务内部抛出异常
- **THEN** 记录 warning 与异常信息，而非直接 `pass`

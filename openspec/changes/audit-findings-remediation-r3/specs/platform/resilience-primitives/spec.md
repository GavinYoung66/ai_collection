# Spec Delta

## ADDED Requirements

### Requirement: 重试分类契约

重试装饰器 MUST 能识别被转译为业务异常的底层可重试错误（沿异常 cause 链判定），或转译 MUST 发生在重试层之外。MUST NOT 出现"声明 N 次指数退避、实际零重试"的契约失真。

#### Scenario: 转译后的超时错误仍被重试

- **WHEN** 底层 HTTP 客户端抛出 `TimeoutError` 并在函数体内被转译为业务异常
- **THEN** 重试层仍识别为可重试错误，按声明执行 3 次退避调用

#### Scenario: 不可重试的业务错误只调用一次

- **WHEN** 下游返回明确的业务拒绝且不属于可重试类别
- **THEN** 只调用一次，不进行无意义重试

### Requirement: 熔断恢复与健康如实

熔断器 MUST 具备半开恢复路径与失败计数清零机制，MUST NOT"只开不关"。健康探针 MUST 如实反映熔断打开状态，MUST NOT 在已打开时报告连通。同一 key 的熔断状态 MUST 全进程单源，MUST NOT 由多个注册表分裂维护。

#### Scenario: 冷却期后恢复

- **WHEN** 熔断打开后经过冷却期，第一次试探请求成功
- **THEN** 熔断关闭、失败计数清零，后续请求正常放行

#### Scenario: 熔断打开时健康探针如实返回

- **WHEN** 某下游熔断处于打开状态，健康检查被执行
- **THEN** 探针返回不健康，不报告 connected

#### Scenario: 同一 key 共享熔断状态

- **WHEN** 同一模型 key 分别经 provider 与可靠性编排器调用
- **THEN** 两入口读写同一熔断实例，状态不分裂

### Requirement: 超时口径单源

LLM 调用的总超时 MUST 由单一配置源定义并被实际调用路径读取。MUST NOT 存在两个互相矛盾的默认值（如 10s 与 120s）或层级声明对实际入口不生效。

#### Scenario: 配置变更对实际调用生效

- **WHEN** 运维修改 LLM 总超时配置
- **THEN** 实际调用路径按新值超时，与其他层级声明一致

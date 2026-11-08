# Spec Delta

## ADDED Requirements

### Requirement: 工具参数边界收敛

网关 MUST 在转发前将下游协议参数（如 `top_k`）收敛到合法范围。下游因参数校验返回的 4xx MUST 视为不可重试，MUST NOT 计入熔断失败次数。

#### Scenario: 越界 top_k 被收敛

- **WHEN** 调用方传入 `top_k=21`、`0` 或负数
- **THEN** 网关转发前收敛为合法区间（1–20），下游不返回参数错误

#### Scenario: 下游参数错误不打开熔断

- **WHEN** 下游对某次调用返回 422 参数校验错误
- **THEN** 该次调用不重试，且不增加熔断失败计数，后续正常调用仍可执行

### Requirement: 出站校验全覆盖

所有出站 HTTP 或 MCP 连接 MUST 在建立连接前经过 common 的 SSRF 校验单一实现。MUST NOT 存在某个工具或客户端直接使用 HTTP 客户端绕过校验。

#### Scenario: 指向云元数据地址的出站被拒

- **WHEN** 任一工具或客户端尝试连接 `http://169.254.169.254/`
- **THEN** 连接建立前被拒绝，不发起实际请求

#### Scenario: 全部出站工具使用同一校验

- **WHEN** 逐一检查全部工具集的出站连接路径
- **THEN** 每个路径均调用同一 SSRF 校验函数，无直连旁路

### Requirement: 请求体读取上限与断连处理

ASGI 中间件读取请求体 MUST 设置累计大小上限（默认 1MB），超限 MUST 返回 413 并停止累积。读取循环 MUST 处理 `http.disconnect` 并提前返回，MUST NOT 在客户端断连后永久挂起。

#### Scenario: 超大请求体被快速拒绝

- **WHEN** 客户端发送 2MB 请求体
- **THEN** 网关在累计读取超过 1MB 时返回 413，内存占用不随请求体无限增长

#### Scenario: 客户端断连后协程退出

- **WHEN** 客户端在请求体发送中途断开连接
- **THEN** 读取协程返回，不出现永久等待后续分片

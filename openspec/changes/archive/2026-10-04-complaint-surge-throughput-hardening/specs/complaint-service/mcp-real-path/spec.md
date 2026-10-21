## ADDED Requirements

### Requirement: Real 模式 HTTP 连接池复用与生命周期

real 模式 MCP 传输 SHALL 复用底层 HTTP 连接：HTTP 客户端 SHALL 以实例级长生命周期对象（开启 keepalive 与连接数上限的连接池）承载多次工具调用，而非每次调用新建并销毁 HTTP 客户端与底层 TCP 连接。MCP 会话层 SHALL 继续遵循网关 stateless_http 语义（不假设跨调用的有状态会话）。复用 SHALL NOT 改变工具调用的入参、返回结构、超时（Agent 侧薄超时为权威取消点）与错误映射（超时/鉴权/HTTP/MCP 错误分类）。服务关停时 SHALL 显式关闭共享 HTTP 客户端释放连接，不得泄漏。

#### Scenario: 多次调用复用底层连接

- **WHEN** real 模式下对同一网关连续发起多次工具调用
- **THEN** 底层 TCP 连接被复用，进程持有的连接数在连接池上限内保持稳定，不随调用次数线性增长

#### Scenario: 复用不改变调用契约与错误映射

- **WHEN** 经复用连接发起的调用成功或分别发生超时 / 401/403 / 其他 HTTP 错误
- **THEN** 返回结构与 MCPTimeoutError / MCPPermissionDeniedError / MCPError 的分类与每次新建连接时一致

#### Scenario: 关停释放连接

- **WHEN** 服务优雅关停
- **THEN** 共享 HTTP 客户端被显式关闭，连接池连接释放，无悬挂连接

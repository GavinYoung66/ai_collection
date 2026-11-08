# Spec Delta

## ADDED Requirements

### Requirement: 出站客户端复用

data-platform 对 qc 与 complaint 的出站调用 MUST 复用长生命周期 `AsyncClient`（包括重试循环内部），MUST NOT 每请求新建客户端。客户端 MUST 在服务关停时释放。

#### Scenario: 高频调用只构造一个客户端

- **WHEN** 服务在 1 分钟内发起 100 次对下游的调用
- **THEN** 连接池客户端构造次数为 1，不随请求数增长

#### Scenario: 关停释放连接池

- **WHEN** 服务收到关停信号
- **THEN** 出站客户端被显式关闭，无悬挂连接

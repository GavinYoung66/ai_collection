## ADDED Requirements

### Requirement: 出口 URL 白名单与 SSRF 防护

网关按可配置的 `base_url` 拼接出站请求时 MUST 校验目标：协议 MUST 限定在白名单内，主机 MUST 命中显式白名单。回环地址、内网网段、链路本地与云元数据地址 MUST 被拒绝，拒绝事件 MUST 记录告警。

#### Scenario: 元数据地址被拒绝

- **WHEN** 配置的 `base_url` 指向链路本地/元数据地址（如 `169.254.169.254`）
- **THEN** 请求在出站前被拒绝并告警，不发起连接

#### Scenario: 非白名单协议被拒绝

- **WHEN** `base_url` 使用白名单之外的协议
- **THEN** 请求被拒绝，不发起连接

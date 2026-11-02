## ADDED Requirements

### Requirement: 认证默认强制与敏感接口授权

生产环境 MUST 默认开启认证；关闭认证 MUST 仅限本地开发，且 MUST 在启动日志中显著告警。承载 PII 或质检结论的接口 MUST 经角色校验，未授权请求 MUST 返回 401/403。MUST NOT 因身份载荷为空（`payload=None`）而放行受限资源。

#### Scenario: 生产默认拒绝匿名访问

- **WHEN** 生产配置下不带凭据请求工单或质检记录接口
- **THEN** 返回 401，响应体不含任何 PII

#### Scenario: 检索接口不放行受限文档

- **WHEN** 未携带身份凭证的请求命中受限文档
- **THEN** 该文档不出现在检索结果集中

### Requirement: 默认凭据与弱口令 fail-fast

JWT 密钥、加密密钥 MUST NOT 以默认占位值运行；服务启动时检测到占位值 MUST 立即失败退出并输出明确原因。种子脚本 MUST 从环境变量或密钥系统读取口令，MUST NOT 硬编码弱口令；生产环境 MUST 拒绝执行种子脚本。

#### Scenario: 占位密钥导致启动失败

- **WHEN** `JWT_SECRET` 取默认占位值
- **THEN** 服务启动失败，日志给出明确的配置缺失原因

#### Scenario: 生产执行种子脚本被拒

- **WHEN** 在生产环境运行种子脚本
- **THEN** 脚本拒绝执行并以非零码退出

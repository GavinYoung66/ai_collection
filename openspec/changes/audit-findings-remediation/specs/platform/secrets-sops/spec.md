## ADDED Requirements

### Requirement: 明文密钥零容忍（含注释行）

密钥载体（`.env.enc`）MUST NOT 含任何明文真实密钥，包括 sops 不加密的注释行。密钥字段值 MUST 仅为密文形式；注释中 MUST NOT 记录真实密钥或其可用于复用的片段。CI MUST 提供密钥扫描门禁，命中 MUST 失败并定位文件与行号。

#### Scenario: 注释行泄漏被拦截

- **WHEN** `.env.enc` 的注释行包含真实 API Key
- **THEN** 密钥扫描门禁失败并输出命中位置

#### Scenario: 密文载体无非密文密钥

- **WHEN** 扫描 `.env.enc` 的密钥字段赋值
- **THEN** 全部为密文形式，不存在明文真实值

### Requirement: 敏感配置格式与占位值 fail-fast

加密密钥、管理员口令等敏感配置 MUST 在启动时校验格式与是否为占位值。格式非法或占位值 MUST 立即失败退出，MUST NOT 延迟到首次使用时才抛错。基础设施口令（如监控面板管理员口令）MUST 纳入同一加密注入体系，MUST NOT 以明文散落在示例文件。

#### Scenario: 非法加密密钥启动失败

- **WHEN** 加密密钥取占位值，或长度/编码格式非法
- **THEN** 服务启动即失败，不进入服务就绪状态

#### Scenario: 监控口令纳入加密体系

- **WHEN** 检查示例环境文件中的监控面板管理员口令
- **THEN** 为占位值且生产值由加密注入提供

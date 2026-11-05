## Purpose

扩展认证与授权基线（第一期已立「认证默认强制」「默认凭据 fail-fast」等需求）：本轮收敛第一期 fail-fast 的覆盖完整性，并新增令牌类型隔离、行级权限 fail-closed、限流身份来源可信三类约束。共同特征：缺陷不在算法本身（JWT 白名单、exp、AES 格式均已正确），而在"覆盖面"与"输入来源"——漏接一个服务、漏校验一个 claim、信任一个客户端可控头，即可绕过既有防线。

## ADDED Requirements

### Requirement: 凭据 fail-fast 接入完整性

所有使用 JWT 验签或 AES 加解密的服务 MUST 在 lifespan 启动时接入 `validate_required_secrets` 占位值/格式 fail-fast，MUST NOT 存在"用密钥验签却未接入 fail-fast"的服务。占位密钥（如 `change_me`）下涉及密钥的服务 MUST 拒绝启动。

#### Scenario: rag-service 漏配密钥即拒绝启动

- **WHEN** rag-service 以 `JWT_SECRET=change_me` 启动
- **THEN** lifespan 启动即失败（非零退出），服务不进入可服务状态，攻击者无法以占位密钥自签 token 绕过行级权限

### Requirement: 令牌类型隔离

验签入口 MUST 校验 `type` claim：业务接口验签只接受 `type=access`；refresh token MUST NOT 能通过业务接口验签（type 不符返回 invalid_token 理由）。refresh token 的生命周期（7 天）MUST NOT 因 type 不校验而事实提升为长效访问凭证。

#### Scenario: refresh token 调业务接口被拒

- **WHEN** 使用有效的 refresh token（`type=refresh`）请求挂 `require_roles` 的业务接口
- **THEN** 验签失败返回 401，不产生任何 role 授权效果

### Requirement: 行级权限 fail-closed

行级权限矩阵文件缺失或损坏时，登录用户的可见类目 MUST 降级为匿名白名单（fail-closed），MUST NOT 静默退化为全量放行。`KB_ROW_LEVEL_ENABLED=true` 但行级权限服务未注入时，MUST 拒绝请求（显式错误），MUST NOT 走无过滤路径。

#### Scenario: 矩阵文件丢失不放大权限

- **WHEN** `conf/row_access_matrix.yaml` 被误删除或解析失败，登录用户执行受限检索
- **THEN** 可见类目收敛为匿名白名单（不含 script/case），并持续输出 error 告警

### Requirement: 限流与审计身份来源可信

限流身份 key MUST 来自服务端可信来源（验签后的用户标识或连接对端 IP 派生），MUST NOT 直接采用客户端可任意伪造的头（如 `X-User-Id`）作为限流维度。代理头（`X-Real-IP`/`X-Forwarded-For`）MUST 经格式校验且仅在显式可信代理配置下采信。匿名可调用接口的限流 MUST NOT 可通过每请求变换客户端头绕过。审计日志中的客户端 IP MUST NOT 采信未经格式校验的可伪造代理头，与限流采用同一来源校验口径。

#### Scenario: 变换伪造头无法绕过限流

- **WHEN** 匿名攻击者每请求携带不同的 `X-User-Id` 高频调用 `kb_search`
- **THEN** 限流按 IP 维度生效，请求被限流拒绝；Redis 限流 key 空间不随伪造头线性膨胀

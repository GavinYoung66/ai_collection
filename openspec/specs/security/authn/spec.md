# security/authn Specification

## Purpose
为网关及任意 Python 服务提供统一的请求鉴权验证逻辑契约：JWT 与 API Key 的校验、身份头下发与豁免清单。验证逻辑下沉至 common/security 作为全平台单一实现，由网关鉴权 sidecar 与各服务按需复用，避免多份鉴权实现漂移。

## Requirements

### Requirement: JWT 验证契约

`common/security/authn.py` MUST 提供 JWT 验证函数，校验项至少包含：签名（使用 `.env` 配置的密钥或 JWKS URL 验签）、过期时间（`exp`，拒绝已过期 token）、签发方（`iss`，须匹配配置值，不匹配则拒绝）、受众（`aud`，须匹配配置值，不匹配则拒绝）。验证通过 MUST 返回用户身份信息（user_id、role），验证失败 MUST 返回明确的拒绝原因（过期/签名错误/issuer 不匹配/audience 不匹配）。JWT 验签 MUST 复用现有 `common/security/rbac.py` 的签发/验签能力（`create_token`/`verify_token` 及 `settings.JWT_SECRET`/`JWT_ALGORITHM`），在其之上补充 iss/aud 校验与拒绝原因分类；若 `verify_token` 现有返回不足以区分原因，MUST 扩展 `rbac.py` 本身，MUST NOT 在 `authn.py` 中另起炉灶再实现一套 JWT 解码。身份字段 `role` 直接取自 JWT payload 的 `role` 字段（角色比较语义与 `require_role.py` 一致，大小写不敏感），MUST NOT 引入第二套角色模型。密钥与 JWKS 配置 MUST 从环境变量读取，MUST NOT 硬编码在代码中。

#### Scenario: 合法 JWT 验证通过

- **WHEN** 提供一个签名有效、未过期、iss/aud 匹配的 JWT
- **THEN** 验证函数返回该 token 的 user_id 与 role

#### Scenario: 过期 JWT 被拒

- **WHEN** 提供一个 `exp` 已过的 JWT
- **THEN** 验证函数返回拒绝，原因标注"token expired"

#### Scenario: 伪造签名被拒

- **WHEN** 提供一个用错误密钥签名的 JWT
- **THEN** 验证函数返回拒绝，原因标注"invalid signature"

#### Scenario: issuer 不匹配被拒

- **WHEN** 提供一个 `iss` 不匹配配置值的 JWT
- **THEN** 验证函数返回拒绝，原因标注"issuer mismatch"

### Requirement: API Key 恒定时间比对

`common/security/authn.py` MUST 提供 API Key 验证函数，使用恒定时间比对（constant-time compare）校验请求携带的 API Key 与配置的合法 Key 列表，防止时序攻击。合法 Key 列表 MUST 从环境变量读取（支持多个 Key），MUST NOT 硬编码。验证通过返回对应身份信息，验证失败返回拒绝。

#### Scenario: 合法 API Key 通过

- **WHEN** 请求携带 `Authorization: Bearer <合法 API Key>`
- **THEN** 验证函数返回该 Key 对应的 user_id 与 role

#### Scenario: 错误 API Key 被拒

- **WHEN** 请求携带不存在的 API Key
- **THEN** 验证函数返回拒绝，且比对耗时与合法 Key 的比对耗时无显著差异（恒定时间）

#### Scenario: 缺少 Authorization 头被拒

- **WHEN** 请求不携带 `Authorization` 头
- **THEN** 验证函数返回拒绝，原因标注"missing credentials"

### Requirement: 鉴权 sidecar 契约

网关鉴权 sidecar（`deploy/auth`）MUST 暴露 HTTP 端点 `/internal/authz` 供 Nginx `auth_request` 调用：接收 Nginx 转发的原始请求头（含 `Authorization`），调用 `common/security/authn.py` 的验证逻辑；验证通过 MUST 返回 HTTP 204 并在响应头中回传 `X-User-Id` 与 `X-User-Role` 供 Nginx 透传给后端；验证失败 MUST 返回 HTTP 401。sidecar MUST 仅监听内部网络（docker network 内），MUST NOT 对外暴露端口。sidecar 的验证逻辑 MUST 完全复用 `common/security/authn.py`，MUST NOT 自行实现第二份验证代码。

#### Scenario: 合法凭证放行并回传身份

- **WHEN** Nginx auth_request 向 `/internal/authz` 转发含合法 JWT 的请求头
- **THEN** sidecar 返回 204，响应头包含 `X-User-Id` 与 `X-User-Role`

#### Scenario: 非法凭证拒绝

- **WHEN** Nginx auth_request 向 `/internal/authz` 转发含伪造 JWT 的请求头
- **THEN** sidecar 返回 401

#### Scenario: sidecar 仅内网可达

- **WHEN** 从外部网络尝试访问 sidecar 的 `/internal/authz` 端口
- **THEN** 连接不可达（sidecar 不对外 publish 端口，仅 docker network 内可达）

#### Scenario: sidecar 复用 common 验证逻辑

- **WHEN** 审查 sidecar 代码
- **THEN** 验证逻辑全部来自 `common/security/authn.py` 的 import，sidecar 内 MUST NOT 存在自行实现的 JWT/API Key 验证代码

### Requirement: 鉴权豁免清单

以下请求 MUST 永远豁免鉴权，不受 `ENABLE_AUTH` 状态影响：① `/gateway/health` 与 `/health` 健康检查端点；② `OPTIONS` 预检请求；③ `/rag/`（Gradio）及其附属资源（`/manifest.json`）——Gradio 自有 `RAG_API_TOKEN`（admin 角色 JWT，见 `.env`）鉴权，网关不重复拦截；④ 纯静态前端壳（`/`、`/complaint/`、`/qc/`）——无业务数据，不纳入网关鉴权。豁免清单由网关层（Nginx location/方法判定）执行，MUST NOT 依赖 sidecar 判断，确保即使 sidecar 不可用，健康检查与预检仍正常工作。

#### Scenario: 健康检查不经鉴权

- **WHEN** `ENABLE_AUTH=true` 时访问 `/gateway/health` 或 `/health`
- **THEN** 请求不触发 auth_request 子请求，直接返回健康响应

#### Scenario: OPTIONS 预检不经鉴权

- **WHEN** `ENABLE_AUTH=true` 时对任意路由发起 `OPTIONS` 预检
- **THEN** 请求不触发 auth_request 子请求，直接返回预检响应

#### Scenario: 豁免不依赖 sidecar

- **WHEN** 鉴权 sidecar 完全停止时访问 `/gateway/health`、`/health` 或发起 `OPTIONS`
- **THEN** 健康检查与预检仍正常返回，不因 sidecar 不可用而失败

#### Scenario: Gradio 与静态前端壳豁免

- **WHEN** `ENABLE_AUTH=true` 时访问 `/rag/`、`/manifest.json` 或静态前端壳（`/`、`/complaint/`、`/qc/`）
- **THEN** 请求不触发 auth_request 子请求：`/rag/` 由 Gradio 自有 `RAG_API_TOKEN` 鉴权，静态壳直接返回

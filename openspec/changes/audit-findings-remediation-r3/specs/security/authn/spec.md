# Spec Delta

## ADDED Requirements

### Requirement: 鉴权覆盖的路由组完整性

所有对外暴露的 HTTP 路由组 MUST 在 router 声明级或端点级接入鉴权（`require_roles` 或等价机制），MUST NOT 存在"同服务其他路由组已鉴权、本组未鉴权"的末梢漏网。演示/灌数/种子路由 MUST 在生产模式拒绝或要求 admin 角色；其签发的访问令牌 MUST 仅在显式 demo 开关开启时可用。MCP 端点的所有 HTTP 方法（含 GET）MUST 纳入鉴权保护或显式返回 405。

#### Scenario: qc demo 灌数端点匿名不可用

- **WHEN** 未认证调用方 `POST /api/v1/qc/demo/seed`
- **THEN** 返回 401/403；生产模式下允许返回与未知路径一致的 404 禁用形态（design D2.1，不暴露 demo 端点存在性），且响应不包含任何可用的 `qc_reviewer` access token

#### Scenario: qc 质检写端点匿名不可用

- **WHEN** 未认证调用方请求 `POST /replay/{session_id}` 或 `POST /check/batch`
- **THEN** 返回 401/403，不触发 DP 拉取、LLM 调用、明细写入与事件发布

#### Scenario: complaint 统计端点匿名不可读

- **WHEN** 未认证调用方请求 `GET /complaints/stats` 或 `GET /complaints/consistency`
- **THEN** 返回 401/403，不返回 verdict 分布、一致率与失败率等运营指标

#### Scenario: MCP GET 方法不被遗漏

- **WHEN** 调用方以 `GET /mcp` 请求 mcp-gateway
- **THEN** 请求进入鉴权中间件；不支持的方法返回显式 405，或未认证请求返回 401，不存在匿名放行路径

### Requirement: 数据面服务间身份与读权限一致

data-platform 数据面（qc-source、pending-qc、snapshot 内部口）MUST 通过服务间凭证（内部 JWT、mTLS 或显式网络隔离声明）限制访问，MUST NOT 以"网关未挂载该路由"作为唯一防护。rag 文档列表/详情读端点 MUST 与 `/search` 使用同一匿名白名单与行级权限矩阵，MUST NOT 因端点不同而可旁路读取受限类目全文。

#### Scenario: 未携带服务凭证读取通话全文被拒

- **WHEN** 未携带内部服务凭证请求 `GET /sessions/{sid}/qc-source`
- **THEN** 返回 401/403，不返回对话全文、客户姓名与逾期金额

#### Scenario: 匿名读取文档详情不放大权限

- **WHEN** 匿名调用方先 `GET /documents` 取 id，再 `GET /documents/{id}` 请求受限类目文档
- **THEN** 列表不含受限类目，详情返回拒绝或过滤后的内容，语义与 `/search` 匿名白名单一致

### Requirement: 工具级角色授权

mcp-gateway 在分发工具调用前 MUST 按调用方身份执行工具级 role ACL。写类工具（如人工复核真值回写）MUST 仅允许具备对应角色的调用方。拒绝 MUST 产生可观测的权限拒绝错误，MUST NOT 静默放行或降级为匿名执行。

#### Scenario: 低权限身份调用写工具被拒

- **WHEN** 不具备写权限的调用方请求 `write_human_review_truth`
- **THEN** 返回 MCP 权限拒绝错误，且目标数据不发生任何变更

#### Scenario: 具备角色身份的调用放行

- **WHEN** 角色满足工具白名单的调用方请求同一写工具
- **THEN** 调用正常分发并返回工具结果

### Requirement: JWT 签发与校验参数对称

JWT 签发端 MUST 写入与校验端对称的 `iss`/`aud` claim（在相应配置存在时），MUST NOT 出现"校验端支持 iss/aud、签发端不写"导致运维配置 issuer 后存量签发链路全量 401。签发与校验 MUST 共用同一配置源；未配置时两端一致地不校验。

#### Scenario: 配置 issuer 后本服务签发 token 可用

- **WHEN** 配置 `JWT_ISSUER` 后由业务服务签发 access token，并以同一配置校验
- **THEN** 校验通过，不出现 issuer mismatch 401

#### Scenario: 未配置时两端行为一致

- **WHEN** 未配置 issuer/aud 时签发并校验 token
- **THEN** 签名与过期校验正常生效，不因缺失 claim 拒绝

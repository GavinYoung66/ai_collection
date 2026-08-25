# RAG Conversational Search Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

### Requirement: 会话上下文持久化（Redis v2 前缀）

系统 SHALL 支持请求头 `X-Session-Id`（ULID 格式字符串，60 字节内）作为多轮标识：缺失时自动生成返回响应头；最近 10 轮 query+命中 doc_id 写入 Redis `rag:session:v2:{session_id}` LIST，TTL=1800s（30min），自动过期清理，禁 KEYS 命令。会话 key 使用 v2 段，不读取 V1 任何 session 数据（V1 无会话）。**请求体中的可选 `session_id` 字段已被移除**：对外多轮标识仅以 `X-Session-Id` 请求头为准，请求体不再接受/忽略该字段（与 V2 代码实现一致，仅移除冗余模型字段）。**会话读写 MUST 纳入 Redis 容错**：Redis 连接失败时静默 bypass 会话读写（等同于单轮 V1 流程），不抛异常、不导致检索接口 500。

#### Scenario: 首次请求无 session_id

- **WHEN** 用户首次检索未携带 X-Session-Id
- **THEN** 系统生成 ULID 作为 session_id，写入 Redis（单轮 query + top1 doc_id），响应头 `X-Session-Id` 返回新值，缓存 key 和日志均记录 session_id

#### Scenario: 请求体携带 session_id 字段被忽略

- **WHEN** 调用方在请求体中传 `{"session_id":"some_id"}`（无 X-Session-Id 请求头）
- **THEN** 系统忽略请求体 session_id 字段（模型已移除该字段），按首次请求处理并生成新的 X-Session-Id 返回，行为与不传该字段完全一致

#### Scenario: 30min 后会话过期

- **WHEN** 用户 31 分钟后复用旧 session_id 继续检索，Redis TTL 已过期
- **THEN** 按单轮 V1 流程处理，不读历史上下文，返回相同 V1 检索结果，degraded=false（过期属正常情况，不视为降级）

#### Scenario: Redis 故障 bypass 会话

- **WHEN** Redis 连接失败（故障降级与 V1 一致），用户请求带 session_id
- **THEN** 静默 bypass 会话读写，等同于单轮 V1 流程，不抛异常给终端，degraded 已由 V1 Redis 故障统一标记

#### Scenario: 会话读取异常不导致检索 500

- **WHEN** session_svc.get_recent_queries 内部抛异常（Redis 超时/序列化错误）
- **THEN** 检索主流程捕获会话异常并继续执行单轮检索，接口返回正常结果（degraded 按 V1 规则标记），不返回 HTTP 500

# RAG Conversational Search Specification

## Purpose

为坐席提供多轮追问式检索：在 Redis 中保存查询上下文，后续追问可携带历史语义补全，缺失会话或会话过期自动回退单轮 V1 流程不中断使用。

## Requirements

### Requirement: 会话上下文持久化（Redis v2 前缀）
系统 SHALL 支持请求头 `X-Session-Id`（ULID 格式字符串，60 字节内）作为多轮标识：缺失时自动生成返回响应头；最近 10 轮 query+命中 doc_id 写入 Redis `rag:session:v2:{session_id}` LIST，TTL=1800s（30min），自动过期清理，禁 KEYS 命令。会话 key 使用 v2 段，不读取 V1 任何 session 数据（V1 无会话）。

#### Scenario: 首次请求无 session_id
- **WHEN** 用户首次检索未携带 X-Session-Id
- **THEN** 系统生成 ULID 作为 session_id，写入 Redis（单轮 query + top1 doc_id），响应头 `X-Session-Id` 返回新值，缓存 key 和日志均记录 session_id

#### Scenario: 30min 后会话过期
- **WHEN** 用户 31 分钟后复用旧 session_id 继续检索，Redis TTL 已过期
- **THEN** 按单轮 V1 流程处理，不读历史上下文，返回相同 V1 检索结果，degraded=false（过期属正常情况，不视为降级）

#### Scenario: Redis 故障 bypass 会话
- **WHEN** Redis 连接失败（故障降级与 V1 一致），用户请求带 session_id
- **THEN** 静默 bypass 会话读写，等同于单轮 V1 流程，不抛异常给终端，degraded 已由 V1 Redis 故障统一标记

### Requirement: 追问上下文自动补全
当 `KB_MULTITURN_ENABLED=true`（默认 false=V1 单轮行为）且 session 存在最近 N 轮历史时，系统 SHALL 在 Query 改写前置步骤中，将最近 3 轮的 query+命中 doc_id title 作为附加上下文送入 LLM 改写接口，产出带上下文语义的 rewritten_query；若改写接口已降级或未启用，则仅基于 V1 检索 + history query 简单拼接（弱补全），兜底不阻断。

#### Scenario: 追问"还有什么要求"自动补全
- **WHEN** 首轮 query="逾期协商需要哪些材料"命中 regulation 文档，第二轮 query="还有什么要求" 且 KB_MULTITURN_ENABLED=true + session 已保存
- **THEN** 检索管道使用带上下文补全后的 rewritten_query="逾期协商还有什么要求（材料提交后流程与金额要求相关）" 或 LLM 改写结果执行召回，最终返回协商后续流程相关片段

#### Scenario: 开关关闭完全回退单轮
- **WHEN** KB_MULTITURN_ENABLED=false（默认），即便用户携带 session_id
- **THEN** 系统不读 session 历史，不做上下文补全，行为与 V1 完全一致（单轮检索），session 可被写入但不读取，不影响检索结果排序

## Purpose

冻结 rag-service 使用的 Redis 限流键与熔断键清单，声明废弃旧的全局 `rag:{resource}:degraded` 键，避免与 complaint/qc 或 rag 内部其它模型/资源互相误杀。

## ADDED Requirements

### Requirement: rag Redis 限流与熔断 key 清单

rag-service 写入或查询的韧性相关 Redis key MUST 仅使用下列带服务前缀的格式（资源段非空、不含冒号）。TTL 熔断标志为 60s 量级，到期自动恢复。

**限流（保持现网，本变更不改名）：**

- `rag:ratelimit:{bucket}:{user}`（用户维；`user` 优先 `X-User-Id`，否则客户端 IP / anonymous）
- `rag:llm_ratelimit:{model}`（由 `invoke_llm` 按模型写入，60/min，非用户维 B0006）

**LLM 熔断 / 降级：**

- `rag:llm_degraded:{model}`（STRING `"1"`，TTL 60s）

**外部 HTTP 熔断（非 MCP，但复用 mcp_degraded key 形态）：**

- `rag:mcp_degraded:embed`
- `rag:mcp_degraded:rerank`
- `rag:mcp_degraded:ocr`
- `rag:mcp_degraded:asr`

系统 MUST NOT 再写入或查询下列废弃全局键：`rag:llm:degraded`、`rag:llm_degraded`（无模型后缀）、`rag:embed:degraded`、`rag:rerank:degraded`、`rag:ocr:degraded`、`rag:asr:degraded`。本变更不迁 Query 改写，`rag:rewrite:degraded` 仍可存在直至另案。查询缓存 `rag:cache:query:v2:*`、敏感词 `rag:sensitive_words:version`、补索引 `rag:backfill:pending` 等非韧性键不在本 Requirement 废弃范围内。

#### Scenario: LLM 熔断只写带模型后缀的键

- **WHEN** LLM 回答失败触发熔断
- **THEN** Redis 出现 `rag:llm_degraded:{model}` 且 TTL 为 60s 量级；`rag:llm:degraded` MUST NOT 被 SET

#### Scenario: embed 熔断使用 mcp_degraded 形态

- **WHEN** Embedding HTTP 连续失败达阈值
- **THEN** 权威熔断 key 为 `rag:mcp_degraded:embed`；`rag:embed:degraded` MUST NOT 被 SET

#### Scenario: 废弃 OCR/ASR 全局键

- **WHEN** OCR 或 ASR 连续失败达阈值
- **THEN** 写入 `rag:mcp_degraded:ocr` 或 `rag:mcp_degraded:asr`；`rag:ocr:degraded` / `rag:asr:degraded` MUST NOT 被 SET

#### Scenario: 限流 key 前缀不变

- **WHEN** 用户维搜索请求计数
- **THEN** 使用 `rag:ratelimit:{bucket}:{user}`，不改为无前缀或其它服务前缀

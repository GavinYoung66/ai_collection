# Proposal: complaint-service-batch3

## Why

M3 投诉研判系统的 LangGraph 编排（批次5）与业务 API（批次7）都依赖两个核心能力层：MCP 客户端（模块3）和 LLM 能力层（模块5）。批次1 脚手架已在 `app/api/deps.py` 预留 `get_mcp_client` / `get_llm_provider` 单例工厂但永久返回 None，批次2 数据层已就绪（complaint_agent_logs.mcp_calls / llm_call_log_ids 字段、redis_keys 的 csmp:llm_degraded Key）。若无 MCP Client 与 LLM Provider，后续 Agent 节点无法取证、无法推理、无法兜底。

本批次交付模块3（3.1~3.2）与模块5（5.1~5.4），打通 MCP 调用封装、网关双模连接、LLM Provider 结构化输出、模型路由四级降级、规则兜底引擎、熔断 Redis 标识，并接线 deps.py。

## What Changes

**模块3 — MCP 客户端（新增）**
- 3.1 `app/mcp/client.py`：`MCPClient.call(server, tool, params) -> dict`，含超时控制、业务类错误重试、服务故障不重试降级、权限拒绝终止+审计、PII 脱敏二次校验、调用审计写入 mcp_calls 结构；异常 `MCPTimeoutError` / `MCPError`
- 3.2 `app/mcp/gateway.py`：`MCPGateway` 支持 `MCP_GATEWAY_MODE=inline`（dev 进程内 Mock）/ `standalone`（prod 独立网关，地址配置注入），连接池管理 + 健康检查钩子

**模块5 — LLM 能力层（新增）**
- 5.1 `app/llm/provider.py`：`ComplaintLLMProvider.invoke(prompt, schema, trace_id, session_id) -> BaseModel`，复用 common.llm，Schema 校验失败重试 3 次，Token 累计，超 max_token_threshold 抛错，response_format=json_object，temperature=0.1/top_p=0.9/seed=42，CoT AES-256-GCM 字段约定
- 5.2 `app/llm/router.py` + `model_router.yaml`：按 Agent 角色路由模型，四级降级链 deepseek-r1 → qwen-plus → qwen-turbo → RuleBasedScriptEngine → 全量熔断，模型名走 yaml 不硬编码
- 5.3 `app/llm/fallback.py`：`ComplaintRuleEngine` 复用 common.llm.rule_engine，6 个 generate_* 方法，输出带 `[规则兜底]` 前缀，verdict 四值无 normal
- 5.4 `app/llm/breaker.py`：`LLMBreaker` 复用 common.llm.circuit_breaker，熔断写 Redis `csmp:llm_degraded=1`（用 batch2 redis_keys）

**接线**
- `app/api/deps.py`：`get_mcp_client` / `get_llm_provider` 不再永久返回 None，可注入真实实现或可切换 Mock

**回补**
- 3.1/5.1 接口定稿后，回写对齐批次0 Mock（P1.1 MockLLMProvider / P1.2 MockMCPClient）的 invoke/call 签名

## Capabilities

### New Capabilities
- `complaint-service/mcp-client`: MCP 客户端统一调用封装 + 网关双模连接（3.1~3.2）
- `complaint-service/llm-layer`: LLM Provider + 模型路由 + 规则兜底引擎 + 熔断管理（5.1~5.4）

### Modified Capabilities
- `complaint-service/scaffold`: `app/api/deps.py` 的 get_mcp_client / get_llm_provider 从永久返回 None 改为可注入真实实现（行为变更，需 delta spec）

## Non-Goals

- 不实现 3.3 / 3.4（外部 MCP 契约对齐，项目管理层，不写代码）
- 不实现 LangGraph 节点/边/State Schema（批次5～6）
- 不实现业务 API（批次7）
- 不实现 Prompt 加载器 / PromptOps（批次4）
- 不接 KMS / 双人授权解密（CoT 本批用配置密钥 stub，字段名与算法定死）
- 不改 `common/`；不新增第二套 MCP/LLM 错误类型体系（优先复用批次0 Mock errors）

## Impact

- **新增代码**：`services/complaint-service/app/mcp/{client,gateway,errors}.py`、`services/complaint-service/app/llm/{provider,router,fallback,breaker}.py`、`services/complaint-service/app/llm/model_router.yaml`
- **修改代码**：`services/complaint-service/app/api/deps.py`（接线）、`services/complaint-service/app/config.py`（扩展 MCP/Mock/CoT 配置）、`services/complaint-service/tests/mock/{llm,mcp}/*.py`（签名对齐回补）
- **新增测试**：`tests/unit/test_mcp_client.py`、`tests/unit/test_llm_provider.py`、`tests/unit/test_fallback.py`
- **依赖**：common.llm（provider / circuit_breaker / rule_engine）、common.security.mask、批次2 redis_keys、批次0 Mock
- **不涉及**：LangGraph 节点/边、业务 API、Prompt 加载器（批次4+）

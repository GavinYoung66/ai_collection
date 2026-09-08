# Design: complaint-service-batch3

## Context

批次1 脚手架 `app/api/deps.py` 中 `get_mcp_client` / `get_llm_provider` 永久返回 None，批次2 数据层已提供 `complaint_agent_logs.mcp_calls` / `llm_call_log_ids` 字段与 `redis_keys.LLM_DEGRADED_KEY`。批次0 已提供 Mock MCP Client 与 Mock LLM Provider（鸭子类型）。本批次在此基础上落地模块3（MCP）与模块5（LLM）。

common 层现状（已探查）：
- `common/llm/provider.py`：提供 `chat()` / `chat_json()` 异步函数（非 BaseLLMProvider ABC）
- `common/llm/circuit_breaker.py`：pybreaker 实例 + `_on_open` / `_on_close` 回调
- `common/llm/rule_engine.py`：`RuleBasedScriptEngine` 类（load_rules / match / fallback_response）
- `common/mcp/`：不存在，MCP 封装需 complaint-service 自建

## Goals / Non-Goals

**Goals:**
- MCP Client 可对 Mock Server 调用返回 dict，超时/错误类型齐全
- MCP 网关支持 inline / standalone 双模切换
- LLM Provider 可返回结构化 Pydantic 输出（Mock 路径可验证）
- RuleBasedScriptEngine 6 个 generate 方法可用，输出带 `[规则兜底]`
- 熔断时写 Redis `csmp:llm_degraded=1`
- deps.py 接线：get_mcp_client / get_llm_provider 不再返回 None

**Non-Goals:**
- 不实现 LangGraph 节点/边（批次5+）
- 不实现业务 API（批次7）
- 不实现 Prompt 加载器（批次4）
- 不对接真实外部 MCP Server（3.3/3.4 外部对齐，不写代码）
- 不实现 KMS 密钥管理（CoT 用配置密钥 stub，接口与字段名定死）
- 不改 common/

## Decisions

### D1: MCP Client 与网关分层

`MCPClient`（client.py）只负责单次调用封装（超时/重试/审计/脱敏），`MCPGateway`（gateway.py）负责连接管理与模式切换。`get_client(server)` 返回绑定网关连接的 `MCPClient`。

- 理由：职责分离，调用方不感知 inline/standalone 差异
- 备选：单文件合并 → 拒绝，双模切换逻辑会污染调用封装

**Server / tool 权威名（对齐 PRD 4.4 + 批次0 Mock `SERVERS`）**：

| server 名 | tools（14） |
|-----------|-------------|
| `business_ticket` | query_case_info / query_history_tickets / query_history_complaints / query_user_history_complaints / get_complaint_ticket |
| `call_qc` | query_call_asr / query_qc_violations / query_call_sessions |
| `rag_kb` | search_regulations / search_response_templates / search_complaint_handling_guidelines |
| `data_platform` | write_session / write_human_review_truth / push_rag_candidate |

禁止使用不存在的别名（如 `case_server`）。错误类型优先 **复用** `tests/mock/mcp/errors.py` 的 `MCPTimeoutError` / `MCPError`（`app/mcp/errors.py` 可 re-export），避免两套异常类。

### D2: LLM Provider 用组合而非继承 common.llm

`ComplaintLLMProvider` 内部调用 `common.llm.provider.chat_json()`，不继承（common 无 BaseLLMProvider ABC）。Schema 校验、Token 累计、CoT 加密由 Provider 层自行处理。

- 理由：common 只提供函数级 chat，无 ABC；组合更灵活，批次0 Mock 已用鸭子类型
- 备选：自建 ABC 让 common 继承 → 拒绝，禁止改 common

### D3: 模型路由表走 yaml，不硬编码

`model_router.yaml` 定义 agent_role → primary_model + fallback_chain。`ModelRouter` 启动时加载，业务代码只调 `route(agent_role)`。

- 理由：模型迭代频繁，改配置不改代码；多环境模型差异（dev/pre/prod）可配置
- 备选：硬编码在 router.py → 拒绝，不可运维

### D4: 熔断 Redis Key 复用批次2

`LLMBreaker` 直接 import `app.utils.redis_keys.LLM_DEGRADED_KEY` 与 `TTL_LLM_DEGRADED`，不重新定义 Key。

- 理由：单一数据源，避免 Key 名不一致

### D5: deps.py 用工厂注入 + Mock 开关

`get_mcp_client()` / `get_llm_provider()` 根据配置返回真实实现或批次0 Mock。默认 dev 用 Mock，prod 用真实。

须在 `app/config.py`（`ComplaintSettings`）扩展：
- `MCP_GATEWAY_URL: str = ""`（standalone 地址）
- `USE_MOCK_MCP: bool = True`
- `USE_MOCK_LLM: bool = True`
- `LLM_COT_KEY: str = "change_me_cot_key_32bytes!!!!!!"`（dev stub，≥32 字节约定在实现注释中说明）

- 理由：批次0 Mock 已就绪，可立即联调；不阻塞启动

### D6: CoT 加密用配置密钥 stub

本批定义 `cot_encrypted` 字段名与 AES-256-GCM 算法，密钥从配置读取（`LLM_COT_KEY`），不接 KMS。接口与字段名定死，后续批次接 KMS 只换密钥来源。

- 理由：KMS 对接属外部依赖，本批不阻塞；字段名与算法提前锁定，避免后续改数据结构

### D7: LLM 审计与 Schema 本批边界

- **`llm_call_logs`**：M0 表已在 `0001_init_tables` 存在，但仓库尚无对应 ORM。本批 Provider 须提供可测试的审计缓冲（如内存 `call_log` / 返回 `llm_call_log_id` 占位），**不强制**本批新建 ORM 模型；可选 raw SQL insert 写表，完整 ORM 可后置。
- **结构化 Schema**：本批单测 / Mock 路径使用**本地最小 Pydantic Schema**（如 `tests` 内临时 `SimpleOut`）或 fixture dict，**禁止**依赖尚未交付的 `app/models/state.py` 中 `VerdictDraft` 等（批次5）。
- **`ComplaintRuleEngine`**：在 `common.llm.rule_engine.RuleBasedScriptEngine` 之上**扩展/包装** 6 个 `generate_*`，不是调用 common 上不存在的同名方法。

## Risks / Trade-offs

- **[Mock 与真实接口漂移]** → 3.1/5.1 定稿后回补批次0 Mock 签名对齐；单测覆盖 Mock 路径
- **[common.llm 无 ABC，Provider 复用方式可能需调整]** → 先探查再组合，禁止假设 BaseLLMProvider 存在
- **[standalone 网关本批无真实服务可测]** → 本批用 stub 连通，健康检查钩子预留，真实对接留给 3.3/3.4
- **[CoT 加密密钥配置化有泄露风险]** → 本批仅 dev 用，prod 由后续批次接 KMS；文档注明
- **[熔断与 router 降级联动时序]** → breaker 写 Redis 后，调度 Agent 下一轮读取 is_degraded()；本批单测覆盖写入与查询

## Migration Plan

1. 部署 app/mcp/ + app/llm/ + model_router.yaml
2. 更新 deps.py 接线，dev 环境默认 Mock
3. 3.3/3.4 外部对齐完成后切换 standalone + 真实模型
4. 回滚：删除 app/mcp/、app/llm/，deps.py 恢复返回 None（不影响已归档批次）

## Open Questions

- standalone 网关的鉴权协议细节（token / mTLS）待 3.3/3.4 外部对齐确认，本批预留注入点
- KMS 密钥来源与双人授权解密流程待安全团队确认，本批用配置密钥 stub

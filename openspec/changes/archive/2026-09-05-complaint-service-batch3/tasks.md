# Tasks: complaint-service-batch3

## 执行顺序

**配置扩展 → 3.1 + 5.1 先行（接口定稿后回补批次0 Mock）→ 3.2 ∥ 5.2 ∥ 5.3 ∥ 5.4 可并行 → deps.py 接线最后**

验证命令中的 `PYTHONPATH` 一律用相对路径：`PYTHONPATH=../..:$PYTHONPATH`（在 `services/complaint-service/` 下执行时指向仓库根）。
环境：conda env `ai_collection`（Python≥3.11）。

---

## 0. 配置扩展（ComplaintSettings）

- [x] 更新 `app/config.py`，新增：
  - `MCP_GATEWAY_URL: str = ""`
  - `USE_MOCK_MCP: bool = True`
  - `USE_MOCK_LLM: bool = True`
  - `LLM_COT_KEY: str`（dev stub，不接 KMS）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.config import settings
  assert hasattr(settings, 'MCP_GATEWAY_URL')
  assert settings.USE_MOCK_MCP is True
  assert settings.USE_MOCK_LLM is True
  assert hasattr(settings, 'LLM_COT_KEY')
  print('0 config OK')
  "
  ```

---

## 3. MCP 客户端

### 3.1 MCP Client 统一调用封装

- [x] 创建 `app/mcp/errors.py`
  - **优先**从 `tests.mock.mcp.errors` re-export `MCPTimeoutError` / `MCPError`（或共享同一实现），禁止两套不兼容异常类
- [x] 创建 `app/mcp/client.py`
  - `MCPClient` 类，核心方法 `async call(server, tool, params, *, trace_id, session_id) -> dict`
  - Server 名仅用：`business_ticket` / `call_qc` / `rag_kb` / `data_platform`（对齐 PRD 4.4；禁止 `case_server`）
  - 超时控制；重试仅业务类错误（最多 3 次指数退避）；服务故障不重试直接降级；权限拒绝终止+审计
  - 调用审计记录 server/tool/params摘要/耗时/状态/错误信息（结构对齐 complaint_agent_logs.mcp_calls）
  - 返回前 PII 脱敏二次校验（复用 `common.security.mask`，明文则脱敏+告警）
  - 【REQ-CJ-002】禁止直连业务库/内部 HTTP；外部能力只走 MCP
  - 默认对接批次0 `tests/mock/mcp/mock_client.py`
- [x] 创建 `tests/unit/test_mcp_client.py`（至少：Mock 成功 call + timeout 抛 MCPTimeoutError）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 - <<'PY'
  import asyncio
  from app.mcp.errors import MCPTimeoutError, MCPError
  from tests.mock.mcp.mock_client import MockMCPClient
  from tests.mock.mcp import errors as mock_errors

  assert MCPTimeoutError is mock_errors.MCPTimeoutError or issubclass(MCPTimeoutError, Exception)
  assert MCPError is mock_errors.MCPError or issubclass(MCPError, Exception)

  async def main():
      mock = MockMCPClient()
      data = await mock.call("business_ticket", "query_case_info", {"case_id": "c1"})
      assert isinstance(data, dict)
      mock.set_mode("query_case_info", "timeout")
      try:
          await mock.call("business_ticket", "query_case_info", {"case_id": "c1"})
          raise SystemExit("expected timeout")
      except mock_errors.MCPTimeoutError:
          pass
      print("3.1 Mock path + errors OK")

  asyncio.run(main())
  PY
  PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_mcp_client.py -v
  ```

### 3.2 MCP 网关双模连接

- [x] 创建 `app/mcp/gateway.py`
  - `MCPGateway` 类，支持 `MCP_GATEWAY_MODE=inline`（dev 进程内 Mock）/ `standalone`（prod，地址 `MCP_GATEWAY_URL`）
  - 连接池管理 + 健康检查钩子
  - `get_client(server: str) -> MCPClient`；server 使用 `business_ticket` 等权威名
  - 与 `app/config.py` 的 `MCP_GATEWAY_MODE` / `MCP_GATEWAY_URL` 对齐
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.mcp.gateway import MCPGateway
  from app.config import settings
  assert settings.MCP_GATEWAY_MODE == 'inline'
  gw = MCPGateway()
  client = gw.get_client('business_ticket')
  assert client is not None
  print('3.2 gateway inline OK')
  "
  ```

---

## 5. LLM 能力层

### 5.1 LLM Provider（投诉研判专用）

- [x] 创建 `app/llm/provider.py`
  - `ComplaintLLMProvider` 类，核心方法 `async invoke(prompt, schema, *, trace_id, session_id) -> BaseModel`
  - 复用 `common.llm.provider.chat_json()`（组合，不继承，common 无 ABC）
  - Pydantic Schema 校验失败重试最多 3 次
  - Token 累计 `total_tokens_consumed`；超 `max_token_threshold` 抛错
  - `response_format=json_object`；`temperature=0.1` / `top_p=0.9` / `seed=42`（可配置但默认硬约束）
  - CoT：AES-256-GCM → `cot_encrypted`，密钥 `LLM_COT_KEY` stub
  - 审计：内存 call_log / 占位 id；**不强制**本批新建 `llm_call_logs` ORM
  - 与批次0 MockLLMProvider 鸭子类型对齐；单测 Schema 用本地最小模型，不依赖 `VerdictDraft`
- [x] 创建 `tests/unit/test_llm_provider.py`（Mock 路径返回 LocalSchema；校验失败重试可测则测）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_llm_provider.py -v
  ```

### 5.2 模型路由 + 四级熔断降级

- [x] 创建 `app/llm/model_router.yaml`
  - 定义 agent_role → primary_model + fallback_chain；模型名不硬编码
  - 路由：调度(不调LLM) / 反思·事实·用户诉求(轻量) / 合规·判责(推理) / 产出物(高阶)
  - 四级降级链：deepseek-r1 → qwen-plus → qwen-turbo → RuleBasedScriptEngine → 全量熔断
  - 熔断阈值：deepseek-r1 连续3次失败或 P95>15s；恢复观察期 1 分钟
- [x] 创建 `app/llm/router.py`
  - `ModelRouter` 类，`route(agent_role) -> str` 加载 yaml
  - 降级期间合规/判责输出标记 `model_degraded=true` + `requires_human_review=true`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.llm.router import ModelRouter
  r = ModelRouter()
  assert r.route('compliance') == 'deepseek-r1'
  print('5.2 route OK')
  "
  ```

### 5.3 规则兜底引擎

- [x] 创建 `app/llm/fallback.py`
  - `ComplaintRuleEngine`：**扩展/包装** `common.llm.rule_engine.RuleBasedScriptEngine`（common 无现成 6 个 generate_*）
  - 6 个 generate 方法：`generate_reflection_report` / `generate_verdict_draft` / `generate_judgment_package` / `generate_compliance_opinion` / `generate_user_demand` / `generate_fact_timeline`
  - 所有输出带 `[规则兜底]` 前缀
  - verdict 枚举四值（agent_violation / user_misunderstanding / compliant_but_poor_experience / pending），不含 normal
- [x] 创建 `tests/unit/test_fallback.py`
  - 覆盖 6 个 generate 方法，断言输出含 `[规则兜底]`，verdict 不含 normal
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_fallback.py -v
  ```

### 5.4 LLM 熔断管理

- [x] 创建 `app/llm/breaker.py`
  - `LLMBreaker` 类，复用 `common.llm.circuit_breaker`（pybreaker 实例）
  - 熔断时写 Redis `csmp:llm_degraded=1`（复用批次2 `LLM_DEGRADED_KEY` + `TTL_LLM_DEGRADED`）
  - 恢复时删除该 Key；`is_degraded() -> bool`
  - 单测可用假 Redis / 内存 stub；无真实 Redis 时不崩
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.llm.breaker import LLMBreaker
  from app.utils.redis_keys import LLM_DEGRADED_KEY
  assert LLM_DEGRADED_KEY == 'csmp:llm_degraded'
  b = LLMBreaker(redis_client=None)
  assert hasattr(b, 'is_degraded')
  print('5.4 breaker API OK')
  "
  ```

---

## 回补批次0 Mock（3.1 + 5.1 定稿后）

- [x] 对齐 `tests/mock/llm/mock_provider.py` 的 `invoke()` 到 5.1 签名（兼容 `*, trace_id, session_id`）
- [x] 对齐 `tests/mock/mcp/mock_client.py` 的 `call()` 到 3.1 签名（兼容 `*, trace_id, session_id`；server 仍为权威四名）
- [x] 验证批次0 原 Mock 测试仍通过（不破坏既有断言）

---

## deps.py 接线

- [x] 修改 `app/api/deps.py`
  - `get_mcp_client()` / `get_llm_provider()` 按 `USE_MOCK_MCP` / `USE_MOCK_LLM` 返回 Mock 或真实实现（**不再返回 None**）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.api.deps import get_mcp_client, get_llm_provider
  assert get_mcp_client() is not None
  assert get_llm_provider() is not None
  print('deps wired OK')
  "
  ```

---

## 全部完成标准

- [x] 配置含 MCP_GATEWAY_URL / USE_MOCK_* / LLM_COT_KEY
- [x] 3.1 可对 Mock（`business_ticket`）调用返回 dict；超时/错误可捕获；异常类与批次0 同源或兼容
- [x] 3.2 MCPGateway 支持 inline/standalone（inline 走 Mock，standalone 可 stub）
- [x] 5.1 invoke 可对 Mock 返回本地 Schema；不依赖批次5 State / VerdictDraft
- [x] 5.2 ModelRouter.route 按角色返回模型；四级降级链在 model_router.yaml
- [x] 5.3 6 个 generate 可用，`[规则兜底]`，verdict 四值无 normal；`test_fallback` 全过
- [x] 5.4 熔断路径可写/模拟写 `csmp:llm_degraded`，`is_degraded()` 可查询
- [x] 批次0 Mock 签名已对齐，原 Mock 测试仍通过
- [x] deps 不再返回 None
- [x] 未实现 LangGraph / 业务 API / Prompt 加载器；未建 3.3/3.4 编码任务；未强制新建 llm_call_logs ORM

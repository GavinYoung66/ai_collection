# complaint-service 外部依赖说明（个人本地项目）

负责人：**Ellis Guo**  
目标：本地跑通、流程畅通、功能齐全即可。当前运行路径以 **Mock MCP / Mock LLM** 为主。

## Mock vs Real（USE_MOCK_MCP）

| 模式 | 开关 | MCP 行为 | 是否需要 rag-service |
|------|------|----------|----------------------|
| **Mock（默认 Demo）** | `USE_MOCK_MCP=true` | `MockMCPClient` fixture（4×14 tools） | **否**；`docker-compose.dev.yml` 中 complaint **不** `depends_on` rag |
| **Real** | `USE_MOCK_MCP=false` | `RealMCPTransport`：`rag_kb` → 进程内 bridge → `POST {RAG_SERVICE_URL}/api/v1/kb/search`；其它 server → `MCP_GATEWAY_URL` | **是**；须配置非占位 `RAG_SERVICE_URL`，否则**启动 fail-fast** |

### Real 必填环境变量

- `RAG_SERVICE_URL` — 例 `http://rag-service:8000`（占位 `change_me` / 空 → `MCPConfigError`）
- `MCP_GATEWAY_URL` — 非 `rag_kb` 工具转发（判责主路径仅需 bridge 即可）
- `USE_MOCK_LLM` / LLM keys — 与 MCP 独立；真 LLM 另有 placeholder fail-closed

### 回滚

保持或改回 `USE_MOCK_MCP=true`（及默认 `USE_MOCK_LLM=true`）。

### Real bridge 真联调 smoke

**留给 Apply-4**（本轮以 httpx mock 契约单测为准，不要求本机拉起真 rag）。

## 边界

- 本地不切真 MCP 网关 stub / 真 LLM，除非自己以后改。  
- 密钥不要硬编码进仓库。  
- 批次11 pre/prod env 里 `USE_MOCK_*=false` 只是配置模板，本地仍用 Mock 验证。
- **`USE_MOCK_MCP=true`（默认演示）时不需要启动 `rag-service` / Milvus / ES / embedding。**  
  投诉图里的 `rag_kb.*` / `call_qc.*` 走 Mock fixture；`docker-compose.dev.yml` 中 complaint **不再** `depends_on` rag-service。  
  接真 MCP 且 `rag_kb` 背后调知识库 HTTP 时，再显式启动 rag 栈。

## Reserved-unused 工具（DEP-04）

- `rag_kb.search_complaint_handling_guidelines`：**本里程碑 reserved-unused**（契约/Mock 保留；图节点未挂接；真路径返回空 list）。

## 相关文档

- 端到端流程（含异常/降级）：[`../flow.md`](../flow.md)
- V5 验收骨架：[`../v5-acceptance.md`](../v5-acceptance.md)
- 平台集成分层：`openspec/changes/platform-integration-boundaries/`（Agent=MCP / API=HTTP / 异步=事件）

## 外部依赖文档

| 文件 | 内容 |
| --- | --- |
| [`mcp-server-tracking.md`](./mcp-server-tracking.md) | 4 Server × 14 tool |
| [`mcp-contract-checklist.md`](./mcp-contract-checklist.md) | 契约对照 PRD §4.4 |
| [`rag-kb-confirmation.md`](./rag-kb-confirmation.md) | RAG 入库 / 质量门槛 |
| [`llm-quota-confirmation.md`](./llm-quota-confirmation.md) | LLM 模型 / 配额占位 |
| [`bizsys-integration-confirmation.md`](./bizsys-integration-confirmation.md) | 推送 / 回调 / 复核 |

## 本地怎么验

```bash
cd /Users/ellisguo/myproject/my_work/ai_collection
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service
curl -s http://localhost:8006/health
PORT=8006 bash scripts/smoke/smoke_complaint_service.sh
```

Mock 回归单测：

```bash
pytest services/complaint-service/tests/test_health.py services/complaint-service/tests/test_mock_batch0.py -q
pytest services/complaint-service/tests/unit/test_mcp_real_path.py -q
```

负责人：**Ellis Guo** · 更新：2026-09-08

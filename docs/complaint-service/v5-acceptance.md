# complaint-service V5 验收

变更：`platform-arch-unification-v5`  
负责人：Ellis Guo  
更新：2026-09-08（Apply-4 执行记录）

## 一键脚本

仓库根、conda `ai_collection`：

```bash
bash scripts/smoke/v5_acceptance.sh
# 可选真联调：
# RAG_SERVICE_URL=http://127.0.0.1:8002 bash scripts/smoke/v5_acceptance.sh
```

脚本保证：**mock + fail-fast 失败则 exit 1**；rag 不可达时 **real smoke 记 BLOCKER、exit 0**（不假装通过）。

## 本轮执行证据（Apply-4）

| 检查项 | 命令 / 条件 | 结果 |
|--------|-------------|------|
| Mock health + batch0 | `pytest …/test_health.py …/test_mock_batch0.py -q` | **23 passed** |
| Fail-fast + bridge 契约 | `pytest …/test_mcp_real_path.py -q` | **9 passed** |
| Common | `pytest common/tests/test_llm_{transport,reliability}.py test_guardrails.py -q` | **11 passed** |
| Live complaint `/health` | `curl http://127.0.0.1:8006/health` | **200** `pg/redis/mcp=ok` |
| Live rag `/health` | `curl http://127.0.0.1:8002/health` | **BLOCKER** 连接失败 |
| Real bridge smoke | `RealMCPTransport` → `POST /api/v1/kb/search` | **未跑通**（见下） |
| 文档 grep | external-deps Mock vs Real；容量模型「50/并发/容量」 | **命中** |
| OpenSpec 规划 | `openspec status --change platform-arch-unification-v5` | **proposal/specs/design/tasks 均为 done** |

## Mock 路径（必过）

前提：`USE_MOCK_MCP=true`（默认）。complaint **不**硬依赖 rag-service。

```bash
pytest services/complaint-service/tests/test_health.py \
       services/complaint-service/tests/test_mock_batch0.py -q
```

可选 compose：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service
curl -s http://localhost:8006/health
```

## Fail-fast（必过）

```bash
pytest services/complaint-service/tests/unit/test_mcp_real_path.py -q
```

期望：

- `USE_MOCK_MCP=false` + `RAG_SERVICE_URL` 空/`change_me` → `MCPConfigError`
- httpx mock：`POST /api/v1/kb/search` → 结果含 `doc_id`

## Common 回归

```bash
pytest common/tests/test_llm_transport.py \
       common/tests/test_llm_reliability.py \
       common/tests/test_guardrails.py -q
```

## Real bridge smoke（环境具备时）

### 成功标准

1. `rag-service` `/health` = 200  
2. `USE_MOCK_MCP=false` 语义下进程内 `RealMCPTransport` 调用  
   `rag_kb.search_regulations` / `search_response_templates`  
3. HTTP 成功；有命中时条目含 `doc_id`（空语料仅证明 HTTP 通也算路径通）

### Apply-4 环境 BLOCKER

- **现象**：`http://127.0.0.1:8002/health` 无法连接（`curl: (7) Failed to connect`）。本机当前未拉起 `rag-service` 及向量栈。  
- **影响**：无法做 live bridge 联调；**不**将 real smoke 记为通过。  
- **Mock + fail-fast 仍绿**（见上表）。

### 复现 / 解除步骤

```bash
# 1) 启动 rag 栈（按本机资源裁剪；至少 rag-service 可 /health）
cd /Users/ellisguo/myproject/my_work/ai_collection
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d \
  postgres redis milvus elasticsearch embedding rag-service
# 等待：curl -s http://127.0.0.1:8002/health → 200

# 2) 再跑验收（含 live bridge）
conda activate ai_collection
export PYTHONPATH=services/complaint-service:$PYTHONPATH
RAG_SERVICE_URL=http://127.0.0.1:8002 bash scripts/smoke/v5_acceptance.sh
```

Demo 人工路径（非本轮强制）：见 [`v5-demo.md`](./v5-demo.md)（`run_graph=true` + human_review）。

## 文档 grep

```bash
rg -n "USE_MOCK_MCP|RAG_SERVICE_URL|Mock vs Real" docs/complaint-service/external-deps/
rg -n "50|并发|容量" requirements_document/step1_项目总览 requirements_document/step3_架构设计 | head
```

## 收口对照（§8）

| P0 Batch | tasks 覆盖 | 状态 |
|----------|------------|------|
| A 叙事 | §1.1–1.5 | [x] |
| B 集成 | §4.1–4.6 | [x] |
| C 平台 | §2 + §3.1–3.3 | [x] |
| F 债务 | §3.4–3.7 | [x] |
| E 轻量 DEP-05 | §5.1 | [x] |
| DEBT-03 | §3.7 | [x] |

Non-Goals 未实现（仍在 proposal）：`Batch-G`、`ARCH-04`、`COMP-07`、`ENG-02`/`ENG-07`、`COMP-01`/`COMP-02`。  
§7 Stretch（ENG-01 / COMP-03）**已做**，见下节；不等于 Batch-D 完成。

## Stretch 说明（Apply-Stretch）

- **ENG-01** call_log tempfile sink：`app/observability/call_log_sink.py` — **Batch-D 最小切片**，不等于 ENG-02/07、COMP-01/02。
- **COMP-03** `evidence_refs` / `doc_id`：API + Demo 可见 — **非原 A–G 必选**，面试加分。

验证：

```bash
pytest services/complaint-service/tests/unit/test_call_log_sink.py \
       services/complaint-service/tests/unit/test_api_routes.py -k "verdict or sink" -q
```

## 结论（Apply-4 + Stretch + hotfix）

- **可合并审阅的门禁**：Mock + fail-fast + 规划完整 + P0 收口 — **满足**  
- **Real smoke**：环境具备时再跑；此前 blocker 见上 — **未假装通过**  
- **hotfix**：graph `allowed_rag_doc_ids` / evidence_refs、complaint Redis degrade、LLM 启动校验、DashScope reliability  
- **下一步**：确认后 `/opsx-archive`

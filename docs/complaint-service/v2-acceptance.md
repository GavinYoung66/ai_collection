# Complaint Service V2 验收步骤

> 个人本地联调：假数据（Mock MCP）+（可选真 LLM）+ 静态人审前端。  
> 端口：后端 **8006**，前端 **8080**。负责人：Ellis Guo。

## 0. 起服

```bash
cd /Users/ellisguo/myproject/my_work/ai_collection
# conda activate ai_collection

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service
curl -s http://localhost:8006/health
```

期望：`"status":"ok"`。`USE_MOCK_MCP` 保持 true。

## 1. 灌种子（V2-1）

```bash
bash services/complaint-service/scripts/mock/seed_complaint_v2.sh
curl -s 'http://localhost:8006/api/v1/complaints?page=1&page_size=20' | python3 -m json.tool | head
```

期望：出现 `case-v2-agent-001` / `case-v2-misunderstand-001` / `case-v2-poor-exp-001`。

## 2. 真图研判

Mock LLM（默认）：

```bash
curl -s -X POST http://localhost:8006/api/v1/complaints/case-v2-agent-001/judge \
  -H 'Content-Type: application/json' \
  -d '{"run_graph":true}' | python3 -m json.tool
```

期望：`task_status` 为 `pending_review`（不因缺 Mock fixture 失败）。

真 LLM（V2-2，若已配置 Key）：

- 见 [`real-llm.md`](./real-llm.md)
- `USE_MOCK_LLM=false`、`USE_MOCK_MCP=true`
- 注入 `LLM_DEEPSEEK_*` / `LLM_DASHSCOPE_*`（勿提交仓库）
- 同上 `run_graph=true`；日志应有 `real_llm_invoke` / `provider_path=real`；产出与固定 Mock 文案可区分
- 首次补 `openai` 后需 rebuild：`docker compose ... build complaint-service`

## 3. 启动人审前端

```bash
cd services/complaint-service/web
python -m http.server 8080
```

浏览器打开 <http://localhost:8080>，确认页眉 API 为 `http://localhost:8006`。

路径：

1. 列表点进 `pending_review` 工单  
2. 查看文本 / verdict / 草稿；展开 Agent logs  
3. **Approve** → 状态 `resolved`  
4. **Close** → 状态 `closed`  

可选：对另一工单点 **Reject rejudge**（需 `reason`，前端默认 `wrong_verdict`）→ 状态回到证据采集相关态。

## 4. 冒烟回归

```bash
PORT=8006 bash scripts/smoke/smoke_complaint_service.sh
```

期望：全部 `[PASS]`。

## 常见失败排查

| 现象 | 排查 |
|------|------|
| health 失败 / connection refused | compose 是否 up；端口是否被占用 |
| 灌数失败 | health；脚本路径；`PYTHONPATH` 是否含 `services/complaint-service` |
| `run_graph` 报缺 fixture / MCP | `USE_MOCK_MCP=true`；确认 `tests/mock` 已挂载；V2 case_id 场景 fixture 是否存在 |
| 前端列表 CORS / Failed to fetch | 后端是否含 CORSMiddleware（重启容器）；是否用 `http://localhost:8080` 打开 |
| Approve 后无法 Close | 需先到 `resolved`；reject 后不会关单 |
| Reject 报 requires reason | 升级后的 API 需带 `reason`；请刷新前端或核对后端代码已热更新 |
| 真 LLM 空成功/报错 | Key 是否注入容器；`.env` 是否含 `USE_MOCK_LLM=false`；执行 `bash scripts/mock/verify_complaint_v2_real_llm.sh`；查看 `real_llm_invoke` 日志 |
| DashScope / DeepSeek 连接 403、Tunnel、Connection error | 本机代理/公司网关可能拦截；临时关闭代理或配置 `HTTPS_PROXY` 后再跑 verify 脚本；Agent 沙箱出网失败不代表本机不可用 |

## 非目标（本验收不做）

真 MCP / 真 RAG 生产库、复杂登录、批量复核台、Skill 运行时编排。

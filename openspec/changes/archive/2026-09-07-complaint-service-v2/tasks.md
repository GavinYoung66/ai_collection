# complaint-service-v2 Tasks

> 执行分段（同一 change，三次 `/opsx-apply` 建议范围）：  
> **Segment A** = §1（V2-1 假数据）→ **Segment B** = §2（V2-2 真 LLM）→ **Segment C** = §3+§4（前端 + 验收）  
> 环境：conda `ai_collection`；仓库根 `/Users/ellisguo/myproject/my_work/ai_collection`；服务根 `services/complaint-service/`；端口 **8006**；MCP 保持 Mock。

**硬约束**

- 不接真 MCP / 真 RAG 生产库；密钥仅环境变量，不入库
- 不重做 LangGraph 拓扑（非阻塞不改）；interrupt↔前端 resume 非验收门禁
- 交付路径在 `services/complaint-service/`；前端钉死 `web/` 静态单页（无 Vite/React/Gradio）

**清单 ID 映射**

| tasks | 清单 |
|-------|------|
| 1.1～1.3 | V2-1.1～V2-1.3 |
| 2.1～2.4 | V2-2.1～V2-2.4 |
| 3.1～3.4 | V2-3.1～V2-3.4 |
| 4.1～4.2 | V2-4.1～V2-4.2 |

---

## 环境与验证命令（共用）

```bash
# 仓库根
cd /Users/ellisguo/myproject/my_work/ai_collection
# conda activate ai_collection   # 本机 Python/脚本时使用

# 起后端（postgres + redis + complaint-service）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service
curl -s http://localhost:8006/health

# 创建示例（灌数脚本落地后优先用脚本；以下为手测模板）
curl -s -X POST http://localhost:8006/api/v1/complaints \
  -H 'Content-Type: application/json' \
  -d '{"complaint_text":"...","case_id":"seed-001","channel":"phone"}'

# 真图研判（complaint_id 以实际返回为准）
curl -s -X POST http://localhost:8006/api/v1/complaints/<complaint_id>/judge \
  -H 'Content-Type: application/json' \
  -d '{"run_graph":true}'

# 前端（Segment C；静态页）
cd services/complaint-service/web && python -m http.server 8080
# 浏览器打开 http://localhost:8080 ；API_BASE=http://localhost:8006

# V1 smoke 回归
PORT=8006 bash scripts/smoke/smoke_complaint_service.sh
```

---

## 1. V2-1 假数据增强（Segment A）

- [x] 1.1 新增 ≥3 类种子投诉场景（违规 / 误解 / 体验差）到 `tests/mock/data/`（或 `scripts/` 等价），每条含 `complaint_text`、建议 `case_id`、期望倾向说明；虚构 PII；验证：三类各 ≥1 且文案可区分（读文件或 `python -c` import 断言）
- [x] 1.2 增强 `tests/mock/mcp/fixtures/`（案件 / ASR 或质检 / 法规或模板）使场景可区分且非空；验证：起服后对一条种子执行上文 `judge`+`run_graph:true`（可仍 `USE_MOCK_LLM=true`），agent-logs/中间产物可见非空证据
- [x] 1.3 提供灌数脚本（如 `scripts/mock/seed_complaint_v2.sh`）对 8006 `POST /api/v1/complaints` 创建全部 V2 种子；验证：执行脚本后 `curl` 列表/GET 可见全部种子

## 2. V2-2 真实大模型（Segment B）

- [x] 2.1 在 `docs/complaint-service/` 写清真 LLM 配置（`USE_MOCK_LLM`、`LLM_DEEPSEEK_*`、`LLM_DASHSCOPE_*`、`model_router.yaml`）；Key 仅占位；验证：文档可据此设置 env（不提交真实 Key）
- [x] 2.2 打通 Provider 装配：`USE_MOCK_LLM=false` 时不走 `MockLLMProvider`，走 `ComplaintLLMProvider` + model_router；验证：单元或最小集成断言工厂选择真路径
- [x] 2.3 本地/容器密钥注入说明 + 确认 `.gitignore` 覆盖 `.env` 等；验证：`git check-ignore -v .env`（或等价）且跟踪文件无真实 Key
- [x] 2.4 按需补齐 `requirements.runtime.txt` 并 `docker compose ... build/up` rebuild；种子工单 `run_graph=true` + 真 Key；验证：状态进入 **`pending_review`**，产出与固定 Mock 文案可区分，失败时错误可见（非静默空成功）

## 3. V2-3 人审前端（Segment C 前半）

- [x] 3.1 在 `services/complaint-service/web/` 落地静态单页 + 启动说明（`python -m http.server 8080`）；后端按需 CORS；验证：浏览器打开 `http://localhost:8080`，`API_BASE=http://localhost:8006`
- [x] 3.2 实现列表 + 详情：展示 id/状态/verdict；详情含投诉文本、草稿、可折叠 agent-logs；验证：对 `pending_review` 工单可读
- [x] 3.3 实现复核：P0 **`approve` +（`reject_rejudge` 或 `escalate` 恰好其一，默认偏好 reject_rejudge）**；调用 `PUT .../review`；验证：浏览器操作后状态与 GET 一致
- [x] 3.4 实现关单：调用 `PUT .../close`；验证：状态 `closed` 且与 API 一致；全程可不依赖 curl

## 4. V2-4 联调验收（Segment C 后半）

- [x] 4.1 编写 `docs/complaint-service/v2-acceptance.md`：起服、灌数、真图、前端人审、关单、常见失败排查（复用上文命令块）；验证：按文档可复现主路径
- [x] 4.2 回归：`PORT=8006 bash scripts/smoke/smoke_complaint_service.sh`；验证：全绿（或合法更新后全绿）

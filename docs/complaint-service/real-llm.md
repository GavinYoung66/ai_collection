# Complaint Service：真 LLM 配置（V2）

> Mock MCP 保持开启；仅关闭 Mock LLM。密钥**禁止**提交 git。

## 开关与路由

| 变量 | 说明 | 建议本地值 |
|------|------|------------|
| `USE_MOCK_LLM` | `false` 走 `RealLLMTransport`（非 `MockLLMProvider`） | `false`（联调真模型时） |
| `USE_MOCK_MCP` | 证据层仍 Mock | **`true`** |
| `LLM_DEEPSEEK_API_KEY` | DeepSeek（`deepseek-r1` 等） | 环境变量 / 本地 `.env` |
| `LLM_DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com` | 可覆盖 |
| `LLM_DASHSCOPE_API_KEY` | 千问（`qwen-turbo` / `qwen-plus`） | 环境变量 / 本地 `.env` |
| `LLM_API_KEY` | 兼容 common；未配 DASHSCOPE 时作兜底 | 可选 |

角色 → 模型见 `services/complaint-service/app/llm/model_router.yaml`（本地默认优先 **DeepSeek**）：

| agent_role | primary | 实际 API model |
|------------|---------|----------------|
| reflection / fact / user_demand / generate_package | `deepseek-chat` | `deepseek-chat` |
| compliance / verdict | `deepseek-r1` | `deepseek-reasoner` |

DashScope（`qwen-*`）仍在 fallback 链；若免费额度耗尽（`AllocationQuota.FreeTierOnly`），transport 会自动回退 DeepSeek。业务节点通过 `agent_role=` 传给 Provider，**不**在节点内硬编码模型名。

`config/dev.env` 中 Key 仅为占位 `change_me`。

## 本地 / Docker 注入

1. 仓库根 `.env`（已被 `.gitignore`：`.env` / `.env.*`，保留 `!.env.example`）：

```bash
USE_MOCK_LLM=false
USE_MOCK_MCP=true
LLM_DEEPSEEK_API_KEY=sk-...
LLM_DEEPSEEK_BASE_URL=https://api.deepseek.com
LLM_DASHSCOPE_API_KEY=sk-...
```

2. 重启服务（compose 已 `env_file: .env`；**必须 recreate** 才会重新读 `.env`）：

```bash
# 推荐一键复验（含 recreate + 真图断言）
bash scripts/mock/verify_complaint_v2_real_llm.sh

# 若仍要显式 rebuild（可选 openai SDK）：
REBUILD=1 bash scripts/mock/verify_complaint_v2_real_llm.sh
```

说明：`RealLLMTransport` 默认用 **httpx** 调兼容 `/chat/completions`，不依赖镜像内是否已装 `openai`。仅配了 DashScope 时，`deepseek-*` 角色会 **回退 qwen-plus**（日志 `deepseek_key_missing_fallback_qwen`）。

> **本机 uvicorn 直跑注意（不经 compose 时）**：限流阈值 `LLM_RATE_LIMIT` / `LLM_RATE_WINDOW`（默认 60 次 / 60 秒 / 模型）在进程启动时只从**真实环境变量**读取，不会自动加载本 `.env` 文件。直跑前须先导入：
>
> ```bash
> set -a; source .env; set +a
> PYTHONPATH=. uvicorn app.main:app --app-dir services/complaint-service --host 0.0.0.0 --port 8006
> ```
>
> `services/complaint-service/scripts/loadtest/run_loadtest.sh` 已内置该 source 步骤并打印生效阈值。compose 部署不受影响（`env_file: .env` 自动注入，改后 recreate）。阈值在 import 时读取一次，任何方式改值后都需重启进程。

3. 确认未跟踪真实 Key：

```bash
git check-ignore -v .env
# 勿把含真实 sk- 的文件 add 进仓库
```

## 如何确认走了真 API（非固定 Mock 文案）

Mock 固定句式示例（stub/Mock LLM 常见）：

- `经核查，坐席存在不当话术…`
- `坐席行为合规，用户对计息规则存在误解。`

真路径可观察：

1. 服务日志出现 `real_llm_invoke` 且 `provider_path=real`、带 `model=`  
2. `response_draft` / `verdict` 与上述固定句**可区分**（措辞随投诉变化）  
3. Key 为占位时：调用应**显式失败**（`real LLM key missing…`），而不是静默返回 Mock 成功  
4. 一键：`bash scripts/mock/verify_complaint_v2_real_llm.sh`

联调命令：

```bash
bash services/complaint-service/scripts/mock/seed_complaint_v2.sh
curl -s -X POST http://localhost:8006/api/v1/complaints/case-v2-agent-001/judge \
  -H 'Content-Type: application/json' \
  -d '{"run_graph":true}' | python3 -m json.tool
```

期望：`task_status=pending_review`（成功时）；失败时 API/日志可见错误。

## Docker 依赖

`requirements.runtime.txt` 仍可含可选 `openai`；真路径不强制。改依赖后：`REBUILD=1 bash scripts/mock/verify_complaint_v2_real_llm.sh`。

## 代理 / 出网

若出现 `APIConnectionError` / `Tunnel connection failed: 403`：检查本机 HTTPS 代理是否拦截 `dashscope.aliyuncs.com` / `api.deepseek.com`，换网络或配置可用代理后再跑 verify。

## Why

V1 complaint-service 已可本地跑通（Mock MCP/LLM + 真图 + curl 人审），但假数据偏骨架、默认 Mock LLM 看不到真实研判质量、人审无界面，不适合日常自测。现在需要在不接真外部 MCP 的前提下，完成假数据增强、真 LLM 接入与最小人审前端。

## What Changes

- 新增 ≥3 类种子投诉场景与灌数脚本/命令；增强 Mock MCP fixtures（案件/ASR/质检/RAG）
- 支持 `USE_MOCK_LLM=false` 走真实 DeepSeek/DashScope（复用 Provider + model_router）；密钥仅环境变量，不入库；真图成功进入 `pending_review`
- 新增最小人审 Web（`services/complaint-service/web/` 静态单页）：列表 / 详情 / **approve +（reject_rejudge 或 escalate 之一）** / close；对接 8006 API
- 新增 V2 验收文档；保证 V1 smoke 不回归
- **非 BREAKING**：默认仍可 Mock LLM；MCP 保持 Mock；不改 V1 图拓扑（除非阻塞真 LLM）

## Capabilities

### New Capabilities

- `complaint-service/v2-seed-data`: 种子投诉与增强 Mock MCP 证据/RAG fixtures、灌数方式
- `complaint-service/v2-real-llm`: 真 LLM 配置、Provider 真路径、密钥注入与失败可见性
- `complaint-service/v2-review-ui`: 本地人审最小静态前端（列表/详情/复核/关单）
- `complaint-service/v2-acceptance`: V2 联调验收步骤与 smoke 回归要求

### Modified Capabilities

- `complaint-service/llm-layer`: 明确 `USE_MOCK_LLM=false` 时不得走 MockLLMProvider；路由仍以 model_router 为准
- `complaint-service/mock-deps`: Mock fixtures 须支撑可区分的 V2 场景（非空证据/法规/模板）

## Impact

- 代码：`services/complaint-service/`（mock fixtures、llm provider/config、可选 CORS、`web/` 静态前端）
- 脚本/文档：`scripts/mock/` 或 `tests/mock/data/`；`docs/complaint-service/`（真 LLM 配置说明、v2-acceptance）
- 依赖：Docker `requirements.runtime.txt` 按需补真调用包；**不接真 MCP Server**；前端无强制 npm 构建
- 环境：端口 8006；`USE_MOCK_MCP=true`；个人项目负责人 Ellis Guo
- 不做：真业务 MCP/真 RAG 库、Skill 运行时编排、复杂登录/批量运营台、强制 interrupt↔前端 resume

## Non-Goals

- 真 MCP Server / 真业务推送系统 / 真 RAG 生产库
- 重做 LangGraph 拓扑（非阻塞不改）
- Cursor Skill 作为研判运行时能力
- 仓库根旁路 `complaint-service/` 树
- Vite/React/Vue/Gradio 运营级前端（本变更钉死静态单页）

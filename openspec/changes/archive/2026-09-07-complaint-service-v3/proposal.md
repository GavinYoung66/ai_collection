## Why

V2 已具备 Mock MCP 富种子、真 LLM（DeepSeek 优先）与静态人审页，但仍依赖 curl/seed 脚本，点列表不会开跑研判，详情 Agent logs 偏薄、按钮英文难懂，无法在浏览器里把「中台推单 → Agent 研判 → 人审 → 关单」演示清楚。需要演示型 V3 前端闭环，帮助把整套智能投诉研判系统搞明白。

## What Changes

- 列表页新增「模拟数据中台推送（3 条）」：一键创建可区分的 3 类场景工单（复用 V2 场景语义；**每次新 complaint_id**），模拟中台推送
- 点击 `pending` 列表行自动触发 `judge(run_graph=true)`（加载中防连点）；已研判行仅打开详情；详情「重新研判」（`resolved`/`closed` 禁用）
- 详情右侧「链路追踪」：judge 路径**落单**后经 `GET .../trace` 展示 Agent / MCP / LLM / RAG；可选 LangSmith 外链（P1，非主验收）
- 详情增加状态机进度条（接单→…→关单），映射 `task_status` / 追踪事件
- 人审按钮中文化：通过 / 驳回重判 / 关单；`closed` 后禁用；附短语义说明
- 新增 V3 演示验收短文档；V2 smoke 不回归
- **非 BREAKING**：默认仍 `USE_MOCK_MCP=true`；真 LLM 开关沿用 V2；不改 LangGraph 拓扑（除非追踪埋点最小必要）

## Capabilities

### New Capabilities

- `complaint-service/v3-demo-ui`: 演示前端交互（模拟推送、点选研判、中文人审、状态条、重新研判）
- `complaint-service/v3-trace`: 研判链路追踪聚合（本地时间线 API/数据 + 详情展示；可选 LangSmith 外链）
- `complaint-service/v3-acceptance`: V3 浏览器演示验收步骤与不回归要求

### Modified Capabilities

- `complaint-service/api-layer`: 新增 `POST .../demo/seed` 与 `GET .../trace`（追踪主契约）；行为与错误码对齐现有信封

## Impact

- 代码：`services/complaint-service/web/`（主）；`app/api/`、追踪聚合服务（若新增）；可观测日志字段最小扩展
- 文档：`docs/complaint-service/v3-demo.md`（或等价）
- 依赖：继续静态前端 + 8006；CORS 保持；LangSmith Key 可选且不入库
- 负责人：Ellis Guo（个人本地项目）
- 不做：真 MCP/真中台回流、登录权限、批量运营台、Skill 运行时、interrupt↔前端 resume、大改图拓扑

## Non-Goals

- 真 MCP Server / 真数据中台 / 真 RAG 生产库
- 登录、RBAC、批量复核台、Vite/React 运营前端
- Skill 作为研判运行时
- LangGraph `interrupt` 与前端 resume 打通（Future）
- 以 LangSmith 为唯一真相源（本地时间线为 P0）

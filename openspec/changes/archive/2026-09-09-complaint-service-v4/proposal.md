## Why

V3 演示已能走通「推送 → 研判 → 模糊通过 → 关单」，但人审只表达「认不认 AI」，无法显式确认投诉成立性（坐席违规 / 用户误解 / 体验差），也缺少「凡投诉必回电」与「真违规证据清单 + 质检事件」。需要 V4 把人审做成可分步的处置闭环，才能讲清真实投诉运营语义。

## What Changes

- 详情人审改为**分步**：定责 → 处置（回电）→ 关单；中间状态可停；状态流 `pending_review` → `pending_callback` → `resolved` → `closed`（见 design D1）
- 成立性：**显式确认或改判**三种结论（AI 初选预填，以证据为基准）；改判不一致 MUST 填原因；去掉模糊「通过」主路径
- 凡投诉：处置步 MUST 发布 `callback.requested` 给外呼/坐席（演示 mock 订阅，契约真实；重复调用幂等）
- 真违规：展示有序证据清单（可空态）；回电处置成功后发 `qc.violation.reported`；**不**在页上选处罚档位
- AI=`pending`：引导再跑 + 补证；可改判为三类后继续；禁止无成立结论关单
- 文案：「退回 AI 重跑」，避免「驳回」被理解成驳用户
- 新增 `docs/complaint-service/v4-demo.md` 与最小 API/事件单测；既有 smoke 不回归
- **非 BREAKING**：默认仍 Mock MCP；不接真外呼/真质检执行

## Capabilities

### New Capabilities

- `complaint-service/v4-review-disposition`: 分步定责 / 回电处置 / 关单的 UI 与 API 行为
- `complaint-service/v4-callback-event`: `callback.requested` 事件契约与必发规则
- `complaint-service/v4-qc-event`: 违规有序证据 + 质检事件契约
- `complaint-service/v4-acceptance`: V4 演示验收文档与不回归

### Modified Capabilities

- `complaint-service/api-layer`: 增补 `disposition/verdict`、`disposition/callback` 与关单前置校验（信封与 A5001 等对齐）
- `complaint-service/v3-demo-ui`: 人审按钮文案与交互从「模糊通过」改为分步成立性确认（requirement 级变更）

## Impact

- 代码：`services/complaint-service/web/`、`app/api/`、`app/service/`（review/session）、`app/events/publisher.py`
- 文档：`docs/complaint-service/v4-demo.md`
- 依赖：沿用 VerdictType 四值（成立确认用其中三类显式选项）、现有 publisher 风格；外呼/质检为事件下游（mock）
- 负责人：Ellis Guo（个人本地项目）
- 不做：真 MCP、真外呼平台、真质检扣分执行、interrupt↔UI resume、登录/SPA、LangSmith 唯一真相源

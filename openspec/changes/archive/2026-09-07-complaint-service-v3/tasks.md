## 1. 段 A — 演示灌数 API + 中文人审/禁用

- [x] 1.1 实现 `POST /api/v1/complaints/demo/seed`：每次生成 3 个**唯一新 id** 的三场景工单；路由挂在 `{id}` 之前；curl 验证非 A5001，且连续两次 seed 得到不相交的共 6 个 id
- [x] 1.2 列表页增加「模拟数据中台推送（3 条）」按钮并调用 demo seed，成功后刷新列表；浏览器点击验证出现 3 条新单
- [x] 1.3 人审按钮改为「通过」「驳回重判」「关单」，保留 API 映射与 reason；文案旁短说明；验证文案为中文
- [x] 1.4 按 `pending_review`/`resolved`/`closed` 禁用逻辑（closed 全禁，关单仅 resolved）；状态切换后按钮态正确

## 2. 段 B — 点选触发研判 + 重新研判

- [x] 2.1 列表行点击：`pending` → `judge(run_graph=true)` + 加载/防连点（宜显示「研判中」），完成后打开详情；浏览器验证进入 `pending_review`
- [x] 2.2 非 `pending` 行点击仅打开详情、不自动 judge；点已关闭/已待审行验证无多余 judge 请求
- [x] 2.3 详情页中文「重新研判」：仅 `pending`/`pending_review`（及约定失败态）可点；`resolved`/`closed` 禁用；可点时再次 judge 并刷新

## 3. 段 C — 落单埋点 + 链路追踪 + 状态条

- [x] 3.1a 在 `judge(run_graph=true)` 路径将 Agent/MCP/LLM/RAG（若有）事件**落单**到该 complaint 可查询存储；真图后存储非空（至少 Agent+LLM）
- [x] 3.1b 实现 `GET /api/v1/complaints/{id}/trace` 聚合查询（events 含类别/名称/时间/耗时/状态）；curl 真图后可见 Agent 与 LLM；不存在返回 A5001
- [x] 3.2 详情右侧「链路追踪」渲染 `/trace` 时间线；无 LangSmith Key 时仍可读；浏览器打开已研判单验证
- [x] 3.3（P1）若存在外部 trace id 且 Key 可用，展示「在 LangSmith 打开」；无 id 时隐藏/禁用且不报错（非主验收必过）
- [x] 3.4 详情生命周期状态条按 design D4 映射；`pending_review` 时高亮「待人审」

## 4. 段 D — 验收文档与回归

- [x] 4.1 编写 `docs/complaint-service/v3-demo.md`：起服、推送、点选研判、状态条映射（引用 D4）、本地追踪、通过、关单、开关与排查；注明 LangSmith 非必过
- [x] 4.2 按 v3-demo 手工走通浏览器主路径五项验收（不含 LangSmith 必过）并记录通过
- [x] 4.3 跑既有 complaint-service smoke（或文档等价命令）全部 PASS，确认 V2 路径不回归
- [x] 4.4 为 demo seed（含路由防吞）与 `/trace` 补最小单测或路由冒烟断言并本地通过

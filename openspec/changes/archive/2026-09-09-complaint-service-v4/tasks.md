## 1. 段 A — 文案与成立性 API/UI

- [x] 1.1 实现 `PUT .../disposition/verdict`：三选一；改判须原因；成功→`pending_callback`；拒绝 pending 结论；TestClient 覆盖成功/缺原因/pending
- [x] 1.2 前端定责步：AI 初选预填、三选一、改判原因、证据/追踪入口；提交后进入处置步 UI
- [x] 1.3 文案改为「退回 AI 重跑」+ 说明定责≠驳用户；页面可见且无歧义主按钮「驳回用户」
- [x] 1.4 定责步在 AI=pending 时展示再跑/补证提示（横幅可与段 D 共用组件）；未改判三类前不可进关单

## 2. 段 B — 分步状态 + callback.requested

- [x] 2.1 TaskStatus 增加 `pending_callback` 并接通 session 流转；单测非法跳步
- [x] 2.2 `PUT .../disposition/callback` 发布 `callback.requested`；幂等不重复发；`list_events` 可观测
- [x] 2.3 前端处置步确认回电；未完成时关单禁用
- [x] 2.4 `PUT .../close` 校验已定责且已回电；缺步失败；TestClient 断言

## 3. 段 C — 违规证据有序清单 + 质检事件

- [x] 3.1 有序证据聚合与 UI（含空态）；违规定责后可见
- [x] 3.2 回电处置成功且坐席违规时发 `qc.violation.reported`（可兼发 case.risk.upgraded）；非违规不发 qc；空证据仍可发
- [x] 3.3 页面无处罚档位；联调或断言确认

## 4. 段 D — pending 引导强化 + 文档与回归

- [x] 4.1 强化 pending 补证文案（录音/电话/短信）与再跑入口可达性；浏览器可见
- [x] 4.2 编写 `docs/complaint-service/v4-demo.md`（D1 状态、事件名、幂等、五项验收）
- [x] 4.3 按 v4-demo 记录五项验收（浏览器清单 + 自动化等价可分离）
- [x] 4.4 smoke 全 PASS；定责/回电幂等/关单前置最小单测通过

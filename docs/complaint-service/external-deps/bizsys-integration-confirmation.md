# 业务系统联调说明（个人项目 · Ellis Guo）

本地用 Mock 推送即可。负责人 / 对接人一律：**Ellis Guo**。

## 1）工单推送

| 项 | 值 |
| --- | --- |
| 接口 | `POST /api/v1/complaints` |
| 必填 | complaint_id, user_id, case_ids, complaint_text, submit_time, source_channel |
| 幂等 | 同一 complaint_id |
| 本方地址 | http://localhost:8006 |
| 对接人 | Ellis Guo |
| 确认人 / 日期 | Ellis Guo / 2026-09-07 |
| 缺口 | 无（本地） |
| 缓解脚本 | scripts/mock/push_complaint.sh、push_batch_complaints.py |

## 2）回调（本地可先不接真系统）

| 项 | 值 |
| --- | --- |
| 幂等键 | session_id + version |
| 重试 | 10s / 30s / 60s |
| 死信 | csmp:callback:dlq |
| callback_url | http://localhost:8006/api/v1/complaints（占位，按需改） |
| hmac / auth | N/A（本地） |
| 对接人 / 确认 | Ellis Guo / 2026-09-07 |
| 缺口 | 本地可不接外部回调 |

## 3）复核

| 项 | 值 |
| --- | --- |
| 接口 | `PUT /api/v1/complaints/{id}/review` |
| 六动作 | approve / modify_verdict / modify_response / reject_rejudge / add_evidence / escalate |
| 四值 | agent_violation / user_misunderstanding / compliant_but_poor_experience / pending |
| 禁止 | normal |
| reject_count | ≤2 |
| 对接人 / 确认 | Ellis Guo / 2026-09-07 |
| 本方自测 | approve → resolved OK（2026-09-06） |

### 十项必显（本地 API 有数据即可，不强制做 UI）

| # | 项 | 落地 |
| --- | --- | --- |
| 1 | 投诉基本信息 | yes（API） |
| 2 | 案件信息 | yes（API/Mock） |
| 3 | 证据链 | yes（API/Mock） |
| 4 | 判责草稿 | yes（API） |
| 5 | 合规意见 | yes（API/Mock） |
| 6 | 用户诉求 | yes（API/Mock） |
| 7 | 答辩初稿 | yes（API/Mock） |
| 8 | 整改建议 | yes（API/Mock） |
| 9 | Agent 链路 | yes（agent-logs） |
| 10 | 操作区 | yes（review API） |

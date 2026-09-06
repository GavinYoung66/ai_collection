# 传统催收业务系统联调确认（14.4 / REQ-CJ-015 / REQ-CJ-038）

> 界面与推送由传统催收业务系统提供；complaint-service 提供 HTTP API。
> 本批 **MUST NOT** 修改投诉 API / callback-service / review-service。
> 本仓 `services/complaint-service` 目前仅有 `/health`，无 `POST /complaints` 实现——契约仍按 PRD 11.1 列出，供对端确认。
> TBD ≠ 已确认。

## 1. 工单推送

| 项 | 约定 | 确认 |
| --- | --- | --- |
| 方法/路径 | `POST /api/v1/complaints` | TBD |
| complaint_id | UUID，传统业务系统工单 ID；幂等键 | TBD |
| user_id | 用户 ID | TBD |
| case_ids | 案件 ID 数组（一对多） | TBD |
| complaint_text | 投诉文本 | TBD |
| submit_time | ISO-8601 | TBD |
| source_channel | phone / app / official_complaint / supervisory | TBD |
| 幂等 | 同一 complaint_id 不重复建 session | TBD |
| owner_team | 传统催收业务系统团队 | — |
| confirmed_by | TBD | — |
| confirmed_date | TBD | — |
| gaps | 本仓无 complaints 路由；对端 URL/鉴权/超时 TBD | — |

本地缓解（本仓文件可能尚未落地，路径仍钉死供后续编码批次使用）：

- `scripts/mock/push_complaint.sh`
- `scripts/mock/push_batch_complaints.py`

## 2. 回调接收

| 项 | 约定 | 确认 |
| --- | --- | --- |
| callback_url | TBD（由对接方线下确认，不写生产内网真实地址以外的密钥） | TBD |
| 幂等键 | `session_id` + `version` | TBD |
| 重试 | 失败 3 次，指数退避 10s / 30s / 60s | TBD |
| 死信 | Redis List `csmp:callback:dlq` | TBD |
| 对账 | 每日扫描 `draft_generated` 超 1h 未确认 | TBD |
| auth / hmac | HMAC 或等价鉴权；密钥由部署/对接方线下确认，**不入库** | TBD |
| owner_team | 传统催收业务系统团队 | — |
| confirmed_by | TBD | — |
| confirmed_date | TBD | — |
| gaps | 对端是否按 session_id+version 去重未确认 | — |

## 3. 复核界面

界面由传统业务系统提供，调用 `PUT /api/v1/complaints/{id}/review`。

### review_action（六动作）

`approve` / `modify_verdict` / `modify_response` / `reject_rejudge` / `add_evidence` / `escalate`

### verdict 四值（禁止 `normal`）

`agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`

### 必显字段（10 项）

1. 投诉基本信息（投诉人脱敏、投诉时间、渠道、内容）
2. 案件信息（关联案件、逾期金额、逾期天数）
3. 结构化证据链（时间序、按案件分组、置信度、ASR 残缺标记）
4. AI 判责草稿（整体 + 逐案 + 理由 + 置信度 + 冲突）
5. 合规分析意见（违规点 + 法规来源 ID）
6. 用户诉求分析（类型 + 情绪 + 二次投诉风险）
7. 对外答辩初稿（全文 + 法规引用）
8. 内部整改建议
9. Agent 执行链路（审计模式）
10. 操作区（确认/修改/驳回/补充证据/转上级 + 复核意见）

### 驳回

- 原因枚举：`insufficient_evidence` / `wrong_verdict` / `bad_response` / `other`
- `reject_count` 硬上限 **2**（超限强制 escalate）

| 项 | 值 |
| --- | --- |
| owner_team | 传统催收业务系统团队 |
| confirmed_by | TBD |
| confirmed_date | TBD |
| gaps | 本仓无 review API；对端 UI 是否覆盖 10 项必显未确认 |

# qc-service/demo-seed Specification

## Purpose

Defines demo-only seed and pending-list APIs that insert ended-call fixtures from data_materials so the QC web page can list wait-for-QC sessions without inserting qc_records.

## Requirements

### Requirement: POST demo/seed 三条定型场景

`POST /api/v1/qc/demo/seed` SHALL 一次创建恰好 3 条已结束、文本就绪、尚无有效 `qc_records` 的通话。三条场景 MUST 为：A 合规核身；B 中危（词库或虚假承诺等）；C 红线（冒充公权力 + 联系第三方等）。话术与场景 MUST 取自 `data_materials`（手册、自律公约、细则等），MUST NOT 另编与材料无关的话术。每次成功 MUST 生成新的 `session_id` / `case_id` / `call_id`，MUST NOT 与历史冲突。

系统 SHALL 插入 `cases`、`call_records`、`dialog_turns`。`dialog_turns.speaker` MUST 为 `ai` 或 `user`，MUST NOT 直接写 `agent`。本接口是演示破例：允许 qc 写入主数据表；生产质检路径仍 MUST NOT 随意 UPSERT 案件。待检 MUST NOT INSERT `qc_records`。成功 `data` SHALL 含 3 个 `session_id`，并可含演示 JWT（`role=QC`，`sub` 指向种子 `users.id`，满足 `reviewer_id` FK）。前端 MUST NOT 直连 PostgreSQL。

#### Scenario: 一次三条且待检无 qc_records

- **WHEN** 调用 `POST /api/v1/qc/demo/seed` 且数据库可用
- **THEN** 返回恰好 3 个新 `session_id`，三行 `call_records` 已结束，`qc_records` 无对应行

#### Scenario: 连续 seed 不撞 ID

- **WHEN** 连续两次成功 seed
- **THEN** 两次返回的 6 个 `session_id` 互不相同

### Requirement: GET demo/pending 只列待检

`GET /api/v1/qc/demo/pending` SHALL 列出本演示已灌、尚无有效 `qc_records`（或仅 PARTIAL 可再检，若实现须在任务中钉死）的会话。响应 MUST 为信封 `code==="0"`，空列表 MUST 为 `items=[]`，不是 A2005。WAIT_TEXT / 待检 MUST NOT 为此列表去 INSERT `qc_records`。

#### Scenario: 灌数后 pending 可见

- **WHEN** 刚 seed 成功且尚未 replay
- **THEN** GET pending 含这 3 个 `session_id`

#### Scenario: 开检后离开 pending

- **WHEN** 某 session 已成功 replay 并插入非 PARTIAL 的 `qc_records`
- **THEN** GET pending 不再包含该 `session_id`

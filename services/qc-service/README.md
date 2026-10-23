# qc-service

离线质检 M1 服务。演示静态页（`web/`，批次 5）对接本服务 `http://localhost:8003`。

## 演示人审契约（批次 5 照抄）

- 误报 / 改分：走 `POST /api/v1/qc/reviews/{qc_record_id}`，`action=CORRECT`，手填 `revised_score` 0–100。
- 本 change **不暴露 DISMISS**（API 仍保留；演示前端不要做该按钮）。
- `CONFIRM`（扣分正确）**不改分**。
- 零扣分（`DONE`）**无人审按钮**。
- 路径参数是 `qc_record_id`，不是 `session_id`。JWT 角色使用规范值 `qc_reviewer` / `supervisor`（与 `common/db/models/user.py` 一致，大小写不敏感）。
- 已 `REVIEWED` 再提交 → `A2007`；CORRECT 缺分或越界 → `A2008`。

## 演示时间线

`GET /api/v1/qc/demo/{session_id}/trace`：本地 Redis 步骤（DB 3，`qc:demo:trace:{session_id}`）。无 LangSmith Key 仍 200。本通未调用 MCP `call_qc`。

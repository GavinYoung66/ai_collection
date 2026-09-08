## Purpose

扩展既有 `scripts/smoke/smoke_complaint_service.sh`，对齐现网路由与 health schema。

## Requirements

### Requirement: 冒烟脚本扩展

系统 SHALL 扩展仓库根 `scripts/smoke/smoke_complaint_service.sh`（默认端口 **8006**，勿另起炉灶），覆盖：
- GET `/health`：body `status=="ok"`；读取 `pg`/`redis`/`mcp`（取值 `ok|degraded`）；**禁止**依赖不存在的 `healthy` 字符串作为唯一判据
- POST `/api/v1/complaints`
- POST `/api/v1/complaints/{id}/judge`
- GET status / verdict / agent-logs
- **PUT** `/api/v1/complaints/mode`，JSON 含 `mode`、`gray_percent`、`signers:["ops","tech"]`

#### Scenario: [PASS]/[FAIL] 格式

- **WHEN** 执行每项检查
- **THEN** 输出以 `[PASS]` 或 `[FAIL]` 开头；失败附原因；末尾汇总

#### Scenario: 退出码

- **WHEN** 全部通过 → 退出 0；任一失败 → 非 0

### Requirement: 与 M0 冒烟风格对齐

系统 SHALL 保持与仓库根 `scripts/smoke/smoke_*.sh` 既有风格一致（日志前缀、超时、错误汇总）。

#### Scenario: 失败汇总

- **WHEN** 存在失败项
- **THEN** 脚本末尾输出失败项汇总列表

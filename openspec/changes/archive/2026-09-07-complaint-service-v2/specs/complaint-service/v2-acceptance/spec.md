## Purpose

Defines V2 end-to-end acceptance documentation and smoke regression so Ellis Guo can reproduce seed → real LLM graph → UI review → close without regressing V1 smoke.

## ADDED Requirements

### Requirement: V2 验收步骤文档

系统 SHALL 提供 `docs/complaint-service/v2-acceptance.md`（或等价路径），覆盖：起服、灌种子、真图研判、前端人审、关单，以及常见失败排查（服务未起、Key 无效、Mock 数据缺失等）。

#### Scenario: 按文档可复现主路径

- **WHEN** 按验收文档逐步执行
- **THEN** 可完成种子创建 → `run_graph=true` → 前端 approve → close

#### Scenario: 含失败排查提示

- **WHEN** 阅读验收文档的排查章节
- **THEN** 至少覆盖服务健康、LLM Key、Mock fixture 缺失三类提示

### Requirement: V1 smoke 不回归

完成 V2 增量后，系统 MUST 仍可通过仓库既有冒烟脚本：`PORT=8006 bash scripts/smoke/smoke_complaint_service.sh`（相对仓库根）全绿，或在合法更新用例后仍全绿。

#### Scenario: smoke 全绿

- **WHEN** 在 V2 变更落地后执行上述 smoke 脚本
- **THEN** 全部检查项 PASS（退出码成功）

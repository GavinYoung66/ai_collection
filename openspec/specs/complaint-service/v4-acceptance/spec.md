# complaint-service/v4-acceptance

## Purpose

Defines V4 browser demonstration acceptance so operators can verify stepped review, callback events, and violation QC signaling without requiring real outbound or QC platforms.

## Requirements

### Requirement: V4 演示文档

系统 SHALL 提供 `docs/complaint-service/v4-demo.md`，说明：起服、推送与研判（可引用 V3）、分步定责、回电事件、违规证据与质检事件、pending 补证、关单；注明 mock 下游与非目标。

#### Scenario: 文档可跟做

- **WHEN** 按 v4-demo 步骤操作
- **THEN** 可在浏览器完成分步人审主路径，无需 curl 作为主路径

### Requirement: 主路径验收项

验收 MUST 覆盖：① 显式确认/改判成立性（改判须原因）；② 定责后未回电不可关单；③ 回电后可观测 `callback.requested`（幂等可验）；④ 违规可见有序证据（可空态）且发出 `qc.violation.reported`；⑤ AI=`pending` 时不可无成立结论关单（可引导再跑/补证，或改判三类后继续）。真外呼/真质检执行 MUST NOT 列为必过。

#### Scenario: 五项可验证

- **WHEN** 按验收清单操作演示环境
- **THEN** 上述五项均可验证通过

### Requirement: smoke 不回归

交付前 MUST 仍能通过既有 complaint-service smoke（或文档等价命令）。

#### Scenario: smoke PASS

- **WHEN** 执行既有 smoke
- **THEN** 全部检查 PASS

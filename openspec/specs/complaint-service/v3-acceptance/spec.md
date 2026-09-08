# complaint-service/v3-acceptance Specification

## Purpose

Defines V3 browser demo acceptance steps so the end-to-end complaint judgment story can be verified without curl, while V2 smoke remains green. LangSmith 外链为可选增强，不纳入主路径五项必过。

## Requirements

### Requirement: V3 演示验收文档

系统 SHALL 提供 `docs/complaint-service/v3-demo.md`（或等价路径）文档，说明：起服、打开前端、模拟推送、点选研判、查看状态条与链路追踪、通过、关单；注明 Mock MCP / 真 LLM 开关、状态条映射（引用 design D4）、常见失败排查；并说明 LangSmith 外链非必过。

#### Scenario: 文档可按步执行

- **WHEN** 执行文档中的演示步骤
- **THEN** 可在浏览器完成推送→研判→人审→关单，无需依赖 curl 作为主路径

### Requirement: 浏览器主路径验收标准

验收 MUST 覆盖：① 推送 3 条（可连续两次验证新 id）；② 点 pending 触发研判；③ 详情可见状态条与**本地**链路时间线；④ 「通过」→「关单」；⑤ 按钮为中文且 closed 后禁用。上述五项 MUST NOT 将 LangSmith 外链列为必过。

#### Scenario: 无 curl 主路径

- **WHEN** 按 V3 验收清单操作
- **THEN** 上述五项均可在浏览器完成且状态与 API 一致

### Requirement: V2 smoke 不回归

合并或交付前 MUST 仍可通过既有 complaint-service smoke（或文档记载的等价命令）；V3 变更不得破坏 V2 已验收的默认路径。

#### Scenario: smoke 通过

- **WHEN** 执行既有 smoke 脚本
- **THEN** 全部关键检查 PASS

## MODIFIED Requirements

### Requirement: 详情展示命中、条款、skip 与人审

详情 MUST 展示 `deduction_total`（扣分）、`compliance_score`（派生兼容分，灰度期可展示）、`risk`、`qc_status`、`qc_result`；MUST NOT 展示 grade。命中表 MUST 含来源 `ac|rule|llm`、`rule_id` 或词、证据、本条扣分 `points`（high 20 / medium 8 / low 2；客户侧 R13 MUST 显示不计入）、制度条款号 `policy_clause` 与条款原文 `policy_text`。RAG 检索片段 `kb_snippet` 若存在 SHALL 独立展示，MUST NOT 与 `policy_text` 混用。`skipped_rules` MUST 展示，文案 MUST 表明本通未审，MUST NOT 称为已合规。

零扣分（`deduction_total=0`）时 MUST NOT 展示人审提交按钮，状态 `DONE` 即为完成。有扣分时 MUST 展示：「扣分正确」映射 `CONFIRM`（不改扣分）；「误报」与「改扣分」均映射 `CORRECT` 且 MUST 手填 `revised_deduction`（`>=0` 整数）。前端 MUST NOT 暴露 `DISMISS`。`REVIEWED` 后上述按钮 MUST 禁用。

#### Scenario: 合规通无人审

- **WHEN** 详情 `deduction_total=0` 且无计分 hit
- **THEN** 无人审提交按钮，`qc_status` 为 `DONE`

#### Scenario: 误报必须手填分

- **WHEN** 用户在待复核详情点击「误报」且未填 `revised_deduction`
- **THEN** 不得提交成功；填入合法值后 `qc_status` 为 `REVIEWED` 且 `deduction_total` 等于所填值

### Requirement: 浏览器主路径不依赖 curl

前端 SHALL 支持：灌数两次 ID 不冲突 → 点合规通出 0 扣分 → 点中危或红线通见扣分与条款 → 人审完成。全程 MUST 可在浏览器完成。台账列表 SHALL 以「扣分」列替代原「合规分」列，MUST NOT 展示 grade 列。

#### Scenario: 连续两次灌数

- **WHEN** 用户连续两次成功点击「模拟三条待质检通话」
- **THEN** 至少累计 6 条不同 `session_id`，不得因固定旧 id 导致第二次失败

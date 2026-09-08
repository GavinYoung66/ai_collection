## MODIFIED Requirements

### Requirement: YAML 元数据与七段结构

每个 YAML MUST 含顶层：`prompt_id`、`version`、`target_model`、`scene`、`change_log`、`guardrails`、`sections`。  
`sections` MUST 含 PRD 8.1 七段键：`role`、`task`、`input_constraint`、`output_schema`、`prohibitions`、`fallback`、**`enum_constraints`**（第七段权威键）。  
清单要求的 few-shot MUST 放在 `sections.few_shot`（附加键，不替代七段任一槽位，**键必须存在**，可空）。  
**禁止**将 `examples` 作为第七段权威键名；**禁止**将 `few_shot` 改名为 `examples`。  
批次0 已交付正文，后续批次**禁止删除/清空正文**；仅补齐缺失字段与段键（占位补齐，不覆盖已有内容）。  
`prompt_id` MUST 与文件名去掉 `.yaml` 一致。  
`guardrails` MUST 使用 PRD 字段名 `require_rag_source`（布尔）；禁止用未在 PRD 登记的 `require_source_id` 作为权威字段。

#### Scenario: safe_load 结构合法

- **WHEN** 对任一模板执行 `yaml.safe_load`
- **THEN** 解析成功，且七段键齐全，并存在 `sections.few_shot`

#### Scenario: 正文未被清空

- **WHEN** 对比批次0 与后续批次的 Prompt 正文（role/task 等核心段）
- **THEN** 正文内容保留，未被删除或清空

#### Scenario: 合规与答辩模板要求法规来源

- **WHEN** 加载 `complaint_compliance.yaml` 或 `complaint_generate_package.yaml`
- **THEN** `guardrails.require_rag_source` 为 `true`

#### Scenario: 答辩长度护栏

- **WHEN** 加载 `complaint_generate_package.yaml`
- **THEN** `guardrails.max_response_length == 800`

#### Scenario: verdict 枚举不含 normal

- **WHEN** 检查 complaint_verdict.yaml 的 prohibitions / enum_constraints
- **THEN** 明确禁止使用已废弃的 "normal" 枚举值

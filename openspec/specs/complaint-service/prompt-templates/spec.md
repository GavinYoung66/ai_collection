# complaint-service/prompt-templates

## Purpose

为 8 个投诉研判 LLM Agent 提供最小可用 Prompt YAML（PRD 八章七段 + PromptOps 元数据）。文件均位于 `services/complaint-service/app/prompt/templates/`。本批不实现 `load_prompt()`（批次4）。

## Requirements

### Requirement: 八个 Prompt YAML 文件就位

系统 SHALL 提供下列文件（相对服务根）：

| 任务ID | 路径 |
|---|---|
| P2.1 | `app/prompt/templates/evidence_case_v1.yaml` |
| P2.2 | `app/prompt/templates/evidence_call_qc_v1.yaml` |
| P2.3 | `app/prompt/templates/complaint_reflection.yaml` |
| P2.4 | `app/prompt/templates/complaint_fact_timeline.yaml` |
| P2.5 | `app/prompt/templates/complaint_compliance.yaml` |
| P2.6 | `app/prompt/templates/complaint_user_demand.yaml` |
| P2.7 | `app/prompt/templates/complaint_verdict.yaml` |
| P2.8 | `app/prompt/templates/complaint_generate_package.yaml` |

#### Scenario: 目录内八文件存在

- **WHEN** 列出 `services/complaint-service/app/prompt/templates/`
- **THEN** 上表 8 个文件均存在

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

### Requirement: target_model 与路由表一致

系统 MUST 按 PRD 8.5 设置 `target_model`：证据/反思/事实/用户诉求 → `qwen-turbo`；合规/判责 → `deepseek-r1`；产出物 → `qwen-plus`。

#### Scenario: 判责与合规用推理模型

- **WHEN** 读取 `complaint_verdict.yaml` 与 `complaint_compliance.yaml`
- **THEN** `target_model` 均为 `deepseek-r1`

#### Scenario: 产出物用高阶模型

- **WHEN** 读取 `complaint_generate_package.yaml`
- **THEN** `target_model` 为 `qwen-plus`

### Requirement: Verdict 枚举硬约束写入 Prompt

`complaint_verdict.yaml`（及相关 few-shot/enum）MUST 将 verdict 限定为  
`agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`，MUST NOT 将已废弃的 `normal` 列为合法 verdict。

#### Scenario: 四值出现且无 normal

- **WHEN** 序列化检查 `complaint_verdict.yaml` 全文
- **THEN** 包含 `compliant_but_poor_experience`，且不将 `normal` 列为可选 verdict 枚举值

### Requirement: 本批验收不依赖 load_prompt

本批验收 MUST 仅使用 `yaml.safe_load` 与结构断言。MUST NOT 将 `common.prompt.loader.load_prompt` 可调用作为本批完成条件（该加载器属批次4 任务 6.1）。

#### Scenario: 无 loader 仍可验收

- **GIVEN** `load_prompt` 尚未实现
- **WHEN** 执行本批 Prompt 验收命令（safe_load + 字段断言）
- **THEN** 验收可通过

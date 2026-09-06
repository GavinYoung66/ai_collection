## Purpose

为 complaint-service 与算法/基建对齐 DeepSeek deepseek-r1 与 DashScope qwen-turbo/qwen-plus 的配额、QPM、单价、网络连通及 reasoning_content 格式提供确认文档，对齐 REQ-CJ-022/023。

## ADDED Requirements

### Requirement: 三模型配额与单价确认表

系统 SHALL 提供 LLM 配额确认文档，路径为 `docs/complaint-service/external-deps/llm-quota-confirmation.md`。文档 MUST 覆盖三行模型：

- DeepSeek `deepseek-r1`
- DashScope `qwen-turbo`
- DashScope `qwen-plus`

每行 MUST 含列：`provider`、`model`、`quota`、`qpm`、`qps`、`unit_price_input`、`unit_price_output`、`unit_price_reasoning`、`endpoint`、`network`、`tls`、`connectivity`、`owner_team`、`confirmed_date`、`gaps`。数值允许 `TBD`；列 MUST 齐全。`qpm` 与 `qps` 均须有列。密钥/口令 MUST NOT 写入仓库，填「由部署/对接方线下确认」。

#### Scenario: LLM 确认文档存在

- **WHEN** 检查 `docs/complaint-service/external-deps/llm-quota-confirmation.md`
- **THEN** 文件存在且为 Markdown

#### Scenario: 三个模型名可检索

- **WHEN** grep 确认文档中的 `deepseek-r1`、`qwen-turbo`、`qwen-plus`
- **THEN** 三个模型名均至少出现一次

#### Scenario: 配额表列齐全

- **WHEN** 检查配额表头
- **THEN** 含 `provider`、`model`、`quota`、`qpm`、`qps`、`unit_price_input`、`unit_price_output`、`unit_price_reasoning`、`endpoint`、`network`、`tls`、`connectivity`、`owner_team`、`confirmed_date`、`gaps`

### Requirement: deepseek-r1 reasoning_content 格式确认

系统 SHALL 在 LLM 确认文档中专节记录 `deepseek-r1` 的 `reasoning_content`（或等价思维链字段）格式：字段名、类型、是否与 content 分列、reasoning token 是否单独计费、与 complaint-service CoT 加密存储（`cot_encrypted` / AES-256-GCM）的衔接说明。DashScope 两模型若无 reasoning 字段，对应单元格 MUST 显式写 `N/A`，不得留空列。本批 MUST NOT 修改 LLM 代码。

#### Scenario: reasoning_content 专节存在

- **WHEN** grep LLM 确认文档中的 `reasoning_content`
- **THEN** 命中至少一次，且文档含字段名/类型/分列/计费说明中的至少三项关键词（`字段`、`token`、`cot_encrypted` 或等价）

### Requirement: 网络连通确认且本批保持 Mock LLM

系统 SHALL 记录各模型 `endpoint` 与 `connectivity`（`ok` / `fail` / `TBD`）及网络形态（公网 / VPC / 专线）。文档 MUST 声明：dev/test 继续 `USE_MOCK_LLM=true`；pre/prod 真调用开关不在本批打开。阻塞影响 MUST 写明：配额或连通未确认则全部 Agent 真推理不可用，缓解为 Mock LLM Provider。

#### Scenario: 连通与 Mock 声明可检索

- **WHEN** 检索 LLM 确认文档
- **THEN** 可 grep 到 `connectivity` 与 `USE_MOCK_LLM`（或「Mock LLM」）

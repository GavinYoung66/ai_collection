# complaint-service/prompt-templates 增量规格

## MODIFIED Requirements

### Requirement: 本批验收不依赖 load_prompt

模板验收 MUST 仅使用 `yaml.safe_load` 与结构断言。加载器路径以 `common.prompt.loader.load_prompt` 为准（loader 已下沉公共层，原 `app.prompt.loader` 路径作废）。

#### Scenario: 无 loader 仍可验收

- **WHEN** 对 `app/prompt/templates/` 下任一 YAML 执行 safe_load + 字段断言（七段键与 `few_shot` 齐全）
- **THEN** 验收可通过，无需调用加载器

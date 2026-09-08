## Purpose

Defines enabling real DeepSeek/DashScope LLM calls for complaint-service while keeping Mock MCP, with secrets only via environment and failures visible to operators.

## ADDED Requirements

### Requirement: 真 LLM 配置可文档化启用

系统 SHALL 文档化启用真模型所需配置：`USE_MOCK_LLM=false`、`USE_MOCK_MCP=true`（保持 Mock MCP）、DeepSeek/DashScope 相关 Key 与 base_url、以及 `model_router.yaml` 角色路由关系；密钥 MUST NOT 以明文提交进 git 跟踪文件。

#### Scenario: 配置说明可按文档启用

- **WHEN** 开发者阅读 `docs/complaint-service/` 中真 LLM 配置说明（或等价短文）
- **THEN** 能据此设置环境变量并理解角色→模型路由，且说明中 Key 仅为占位

#### Scenario: 仓库无真实密钥

- **WHEN** 检查 git 跟踪的配置样例与文档
- **THEN** 不含可用的真实 API Key 明文

### Requirement: USE_MOCK_LLM=false 走真实 Provider

当 `USE_MOCK_LLM=false` 时，系统 MUST 通过 `ComplaintLLMProvider`（复用 common LLM 与 `model_router`）发起真实模型调用，MUST NOT 再选择 `MockLLMProvider` 作为默认路径。

#### Scenario: 关闭 Mock 后不再固定 Mock 文案并进入人审

- **WHEN** `USE_MOCK_LLM=false` 且密钥有效，对种子工单执行 `run_graph=true`
- **THEN** 任务进入 `pending_review`（或 API 约定的待人审态），且 `verdict` / `response_draft`（或等价产出）与固定 Mock 模板句式可区分

#### Scenario: 模型失败不静默空成功

- **WHEN** 真模型调用失败或不可用
- **THEN** API/任务状态 MUST 暴露可观察错误或转人工标记，不得返回看似成功的空研判结果

### Requirement: 运行时依赖支持真调用

Docker/本地运行镜像 MUST 包含真 LLM 调用所需运行时依赖；若缺失，MUST 通过 `requirements.runtime.txt`（或等价清单）补齐并可 rebuild。

#### Scenario: 缺包可修复后 rebuild

- **WHEN** 真调用因缺少依赖失败且依赖清单已补齐
- **THEN** rebuild 后同一配置下可再次发起真模型调用

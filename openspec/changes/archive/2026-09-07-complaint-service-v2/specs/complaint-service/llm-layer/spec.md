## ADDED Requirements

### Requirement: V2 关闭 Mock LLM 时的真实调用路径

系统 SHALL 在 `USE_MOCK_LLM=false` 时使用 `ComplaintLLMProvider` 的真实调用路径（复用 common LLM 与 `model_router.yaml` 角色路由），MUST NOT 默认委派给 `MockLLMProvider`。

#### Scenario: 配置关闭 Mock 后走真路径

- **WHEN** 环境为 `USE_MOCK_LLM=false` 且 Provider 工厂/装配被触发
- **THEN** 研判链路使用真实 Provider，而非 MockLLMProvider

#### Scenario: 路由仍由 model_router 决定

- **WHEN** 真路径下按 agent 角色发起调用
- **THEN** 所选模型名仍来自 `model_router.yaml`（或等价配置），不得在业务节点硬编码模型名

## ADDED Requirements

### Requirement: 熔断成功语义

熔断器记录成功 MUST 仅在 `closed` 或 `half-open` 态生效。`open` 态上报的成功 MUST NOT 清零失败计数、MUST NOT 改变熔断状态。状态迁移 MUST 仅由失败阈值、重置窗口与探针结果驱动。

#### Scenario: open 态成功上报不改状态

- **WHEN** 熔断处于 `open`，调用方上报一次成功
- **THEN** 失败计数与熔断状态均保持不变，重置窗口不延长

### Requirement: 重试与熔断实现单源

全平台 MUST 仅保留一个熔断器实现与一个重试原语。语义重复的并行实现 MUST 删除或收敛至统一零件库，MUST NOT 保留两套语义漂移的实现供不同模块各自引用。

#### Scenario: 死实现被移除

- **WHEN** 检索平台公共层的熔断器实现
- **THEN** 仅存在一个被实际引用的实现，另一实现及其导出已删除

#### Scenario: 重试语义统一

- **WHEN** LLM 调用、MCP 调用、消息消费三处需要重试，或公共层历史重试工具需要收敛
- **THEN** 均复用同一重试原语配置重试次数与退避，不各自实现；历史工具（如 `common/utils/retry.py` 装饰器）以兼容入口重导出统一原语，不保留独立实现

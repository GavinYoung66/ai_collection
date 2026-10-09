## Purpose

CI 门禁与 PR 自检：push/PR 触发单元与集成测试，核心模块覆盖率 ≥85% 门禁，其余检查项按项目既有配置启用；覆盖率口径见 design D8。

## Requirements

### Requirement: PR 自检清单

系统 SHALL 提供 PR 自检清单，清单须含：单测、核心覆盖率、冒烟（若跑）、无破坏既有测试、错误码符合 common.errors.codes、未新建第二 Publisher。

#### Scenario: 清单可见

- **WHEN** 开发者提交 PR
- **THEN** 自检清单在 PR 描述或 CI 注释中展示

## Purpose

CI 门禁与 PR 自检；覆盖率按 design D8。

## ADDED Requirements

### Requirement: CI workflow

系统 SHALL 新增 `.github/workflows/ci.yml`，push/PR 执行：
1. unit 测试
2. integration 测试（默认 Fake）
3. **核心模块**覆盖率 ≥85%（精确 `--cov` 列表，非整包强制）
4. 冒烟可选（服务可用时）
5. lint/type：项目已有配置则启用，否则跳过

#### Scenario: CI 门禁可跑通

- **WHEN** PR 触发 CI
- **THEN** 约定检查项通过，否则失败阻塞合并

### Requirement: PR 自检清单

系统 SHALL 提供 PR 自检清单，清单须含：单测、核心覆盖率、冒烟（若跑）、无破坏既有测试、错误码符合 common.errors.codes、未新建第二 Publisher。

#### Scenario: 清单可见

- **WHEN** 开发者提交 PR
- **THEN** 自检清单在 PR 描述或 CI 注释中展示

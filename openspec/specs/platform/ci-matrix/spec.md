# platform/ci-matrix Specification

## Purpose
为全仓库所有服务提供统一的 CI 测试门禁：各服务依赖独立安装、测试独立执行、结果并行汇聚，任一服务测试失败即阻塞合并，避免「只验单服务、其余服务带病合入」。

## Requirements

### Requirement: 六 job 并行的全服务矩阵门禁

系统 SHALL 在 `.github/workflows/ci.yml` 中以矩阵方式提供 6 个并行 job：
1. 五个有实质测试的服务各一个独立 job：data-platform、rag-service、qc-service、mcp-gateway、complaint-service；
2. 一个公共 job：执行 common/ 下全部测试，并附加 call-service、case-service、review-service 三个脚手架服务的 health 测试。

PR 与主干推送 SHALL 触发上述全部 6 个 job。六个 job 相互独立、并行执行；任一 job 失败，整体 CI 判定失败，且 PR MUST 处于不可合并状态。call/case/review 在本能力下只允许执行 health 测试，MUST NOT 为其新增业务测试或业务实现。

#### Scenario: PR 触发六 job 并行

- **WHEN** 向仓库提交 PR
- **THEN** CI 出现 6 个并行 job（5 个服务 job + 1 个 common/脚手架 job），分别覆盖 data-platform、rag-service、qc-service、mcp-gateway、complaint-service，以及 common 与 call/case/review health

#### Scenario: 任一 job 失败阻塞合并

- **WHEN** 6 个 job 中任意一个执行失败
- **THEN** 整体 CI 状态为失败，PR 不可合并；其余 job 仍执行完毕以暴露全部问题

#### Scenario: 脚手架服务仅跑 health

- **WHEN** common/脚手架 job 执行
- **THEN** call-service、case-service、review-service 仅运行各自的 health 测试，不执行也不存在任何超出 health 范围的测试要求

### Requirement: 服务依赖独立安装与测试独立可运行

每个服务 job SHALL 按该服务实际存在的依赖文件安装依赖（根 `requirements.lock`，以及服务自有的 `requirements.runtime.txt`、`pyproject.toml` 等，存在即安装），不得套用其他服务的依赖清单。四个非投诉矩阵服务（data-platform、rag-service、qc-service、mcp-gateway）与三个脚手架服务（call、case、review）的服务目录 SHALL 能在不依赖其他服务目录的情况下独立运行测试：在服务目录下以 `PYTHONPATH=../..` 执行 `pytest tests/ -q` 可独立跑通。complaint-service 不以裸 `pytest tests/ -q` 为准入命令（其覆盖率门禁只统计 unit/integration 阶段，且含冒烟等需起服务的阶段），其独立可运行入口为「complaint-service 覆盖率门禁保留」需求定义的分阶段命令；上述分阶段命令 SHALL 同样可在该服务目录下脱离其他服务目录独立执行。本能力只运行仓库中已存在的测试，MUST NOT 新增、删除或改写任何测试用例，MUST NOT 修改业务代码。

#### Scenario: 非投诉服务与脚手架测试可独立运行

- **WHEN** 在 data-platform、rag-service、qc-service、mcp-gateway 或 call/case/review 任一目录下执行 `PYTHONPATH=../.. pytest tests/ -q`
- **THEN** 该服务测试独立跑通，无需先运行或安装其他服务目录的内容

#### Scenario: 投诉服务按分阶段命令独立运行

- **WHEN** 在 services/complaint-service 下执行其分阶段测试命令（unit+integration、e2e、regression、smoke）
- **THEN** 各阶段独立运行，不要求先运行其他服务目录；裸 `pytest tests/ -q` 不作为该服务的 CI 准入判定

#### Scenario: 依赖按服务各自文件安装

- **WHEN** CI 为某个服务安装依赖
- **THEN** 仅安装根 lock 与该服务自身存在的依赖文件所声明的依赖；某服务缺少某类依赖文件时不报错、不以他服务文件替代

### Requirement: complaint-service 覆盖率门禁保留

complaint-service job SHALL 保留其既有的核心模块覆盖率门禁：单元/集成测试对既有精确核心模块列表统计覆盖率，阈值 ≥85%，不达标即 job 失败；并保留其既有的 E2E、prompt 回归、冒烟等测试阶段。其余四个服务 job 与 common/脚手架 job MUST NOT 设置任何覆盖率强制门禁。

#### Scenario: 投诉服务核心覆盖率不达标即失败

- **WHEN** complaint-service 的核心模块覆盖率低于 85%
- **THEN** 该 job 失败并阻塞合并

#### Scenario: 其余服务无覆盖率门禁

- **WHEN** data-platform、rag-service、qc-service、mcp-gateway 或 common/脚手架 job 运行测试
- **THEN** 即使覆盖率为 0 或未统计覆盖率，只要测试通过，job 即为成功

### Requirement: lint 按需启用

CI SHALL 沿用现状的 lint 策略：仅对存在 lint 配置的服务（仓库当前为 complaint-service 与 mcp-gateway，配置在各自 `pyproject.toml`）安装并执行对应检查，无配置的服务跳过。lint 结果保持既有「advisory 展示、不阻塞 job」语义，本能力 MUST NOT 因引入矩阵而把既有 lint 提示升级为合并阻塞项，也 MUST NOT 新增 lint 配置或改写源码去消除存量告警。

#### Scenario: 无 lint 配置的服务跳过

- **WHEN** 某服务不存在 lint 配置
- **THEN** 该服务 CI 跳过 lint 且不因此失败

#### Scenario: 有 lint 配置的服务执行并仅展示结果

- **WHEN** 某服务存在 lint 配置
- **THEN** CI 安装并执行对应 lint 检查，结果在 CI 输出中可见；存量 lint 告警不改变该 job 的成败（测试通过即成功）

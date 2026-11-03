## MODIFIED Requirements

### Requirement: lint 按需启用

CI SHALL 沿用"仅对存在 lint 配置的服务执行检查"的范围约定；无配置的服务 MUST 跳过且不因此失败。

检查结论 SHALL 由 advisory 升级为**阻断**：ruff 与类型检查（mypy）MUST 在存在配置的服务上作为合并阻塞项执行，MUST NOT 使用 `continue-on-error`。存量告警 MUST 通过显式基线文件冻结（按文件/按规则登记），基线之外新增即失败；基线 MUST 只降不升。

#### Scenario: 无 lint 配置的服务跳过

- **WHEN** 某服务不存在 lint 或类型检查配置
- **THEN** 该服务 CI 跳过对应检查，且不因此失败

#### Scenario: 有 lint 配置的服务执行并仅展示结果

- **WHEN** 某服务存在 lint 或类型检查配置
- **THEN** CI 安装并执行对应检查，结果在 CI 输出中可见；自本 change 起，该结果同时参与合并判定——存量基线内的告警仅展示不阻断，基线外的告警阻断

#### Scenario: 新增告警即阻断

- **WHEN** 某服务在基线之外新增一条 ruff 或 mypy 告警
- **THEN** 该 job 失败并阻塞合并

#### Scenario: 存量基线不被放大

- **WHEN** 基线文件记录的告警数量未被显式下调
- **THEN** CI MUST 拒绝基线数量上调的提交

## ADDED Requirements

### Requirement: 依赖漏洞扫描与真实依赖集成测试

CI MUST 对依赖清单执行漏洞扫描（如 `pip-audit`），发现已知高危漏洞 MUST 失败。依赖版本 MUST 收敛至单一全局清单，MUST NOT 通过服务级旁路清单规避扫描。

测试分层 MUST 区分"契约断言"与"真实依赖集成测试"：仅做结构断言的测试 MUST NOT 被当作真实集成测试的替代；关键外部依赖（数据库、语音服务）MUST 至少各有一条真实依赖路径的集成测试，并可在 CI 中按开关启用。

#### Scenario: 高危漏洞阻断合并

- **WHEN** 依赖清单中存在已知高危漏洞版本
- **THEN** 漏洞扫描 job 失败，合并被阻塞

#### Scenario: 真实依赖集成测试存在

- **WHEN** 检查数据库与语音服务的测试目录
- **THEN** 各自存在可执行的真实依赖集成测试，而非仅结构契约断言

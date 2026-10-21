## REMOVED Requirements

### Requirement: CI workflow

**Reason**: 该需求定义的单服务 CI（写死 complaint-service 的 workflow、unit/integration、核心覆盖率 ≥85%、冒烟、lint）已被平台级能力 `platform/ci-matrix` 取代：CI 重构为全仓库 6 job 并行矩阵，投诉服务成为其中一个矩阵目标，其核心模块 ≥85% 覆盖率门禁及 E2E/prompt 回归/冒烟等阶段在平台 spec 中完整保留并继续生效。继续在本 capability 维护同一份 CI 行为会产生两个相互冲突的事实源。

**Migration**: CI workflow 的行为契约以 `platform/ci-matrix` 为唯一事实源；complaint-service 的特有门禁（核心模块精确 `--cov` 列表 ≥85%、E2E、prompt 回归、smoke）在该平台能力的「complaint-service 覆盖率门禁保留」需求中继承。「PR 自检清单」需求不属于 CI workflow 定义，仍保留在本 capability 中不变。

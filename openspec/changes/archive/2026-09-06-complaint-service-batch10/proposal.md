# complaint-service-batch10 — 测试·CI·冒烟

## Why

M3 投诉研判系统的业务能力（图、API、灰度降级、事件联动）已在批次0～9 落地，但测试体系尚停留在单测层，缺少集成/E2E/冒烟/CI/Prompt 回归/压测的完整闭环。本批次补齐 12.1～12.7，使核心路径可被持续验证、CI 可卡门禁、回归有 golden 基线、压测有准入指标，为批次11 部署与批次12 上线提供可证明的质量依据。

## What Changes

- **12.0 前置最小接线（允许改业务代码，范围钉死）**：`complaint_service.judge(run_graph=True)` 内直接调用 `build_complaint_graph` + `ainvoke`（或本批新增极薄 `app/graph/runner.py`）；`run_graph=False` 保持 stub。**禁止**发明不存在的 `graph_service`；**禁止**无关业务重构。
- **12.1 单元测试强化**：补齐/强化 state reducer、edges、fallback、mcp_client、evidence_helper 等；外部依赖全 Mock；**核心五模块**覆盖率 ≥85%（口径见 design D8）。
- **12.2 集成测试**：图全路径、扇出、`reject_rejudge`→evidence_re_gather、11 降级（**直接调用** `DegradeLLM/MCP/Flow`，不假装图已挂载 degrade）；PG+Redis 可切换。
- **12.3 E2E**：接单→judge(`run_graph=true`)→status→verdict→**PUT** review→**PUT** close→Pub-Sub→回流 Mock。
- **12.4 冒烟**：扩展仓库根 `scripts/smoke/smoke_complaint_service.sh`（默认端口 **8006**）；health 校验 body `status=="ok"` 与 `pg/redis/mcp`；mode 为 **PUT** `/api/v1/complaints/mode` + `signers`。
- **12.5 CI**：新建 `.github/workflows/ci.yml` + PR 自检；有则启用 ruff/bandit/mypy。
- **12.6 Prompt 回归**：golden_set + 一致率 ≥95%；verdict **仅四值**（无孤立 `compliant`）。
- **12.7 压测**：locust + 脚本；复用 `docker-compose.dev.yml`；指标为 **stub 基线**（非 PRD 生产 SLA）；多副本标批次11。

## Capabilities

### New Capabilities
- `complaint-service/unit-testing`
- `complaint-service/integration-testing`
- `complaint-service/e2e-testing`
- `complaint-service/smoke-testing`
- `complaint-service/ci-gate`
- `complaint-service/prompt-regression`
- `complaint-service/load-testing`

### Modified Capabilities
（无既有 capability 的行为变更要求；本批仅新增测试/CI 能力 + 12.0 最小接线）

## Impact

- **允许修改（仅 D1）**：`app/service/complaint_service.py`（judge `run_graph=True` 分支）；可选新增 `app/graph/runner.py` 薄封装。
- **新增目录**：`tests/integration/`、`tests/e2e/`、`tests/regression/`、`tests/loadtest/`、`golden_set/`。
- **修改既有**：`scripts/smoke/smoke_complaint_service.sh`；`tests/conftest.py`（fixture）。
- **新增**：`.github/workflows/ci.yml`；`scripts/loadtest/run_loadtest.sh`（服务内或仓库约定路径）。
- **禁止**：新建 `app/service/event_publisher.py`；旁路根目录 `complaint-service/`；Skill 架构；批次11 Docker 定稿；图节点挂载 `degrade_*`（本批 Non-Goals）；`rate_limiter` 挂 API 主路径（本批仅单测可覆盖既有模块）。

## Non-Goals

- 批次11 Dockerfile / 多环境定稿 / 多副本压测
- Skill 架构改造
- 真 MQ 集群
- 将批次9 `degrade_*` **挂入** LangGraph 节点（11 场景测试走服务层直调）
- `rate_limiter` 接入 API/图主路径
- 无关业务重构、推倒 stub 默认路径

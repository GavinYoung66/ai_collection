# complaint-service-batch10 — 测试·CI·冒烟 Design

## Context

批次0～9 已归档。当前测试以 unit 为主，缺集成/E2E/冒烟/CI/Prompt 回归/压测。

**现状（影响本批）：**

1. **judge stub**：`judge(..., run_graph=False)` 写死 verdict，不跑图；API 已有 `run_graph` 字段，但分支未实现。
2. **无 `graph_service`**：现网入口是 `app.graph.builder.build_complaint_graph`；禁止规划虚构模块名。
3. **health 假绿**：HTTP 恒 200；body：`status:"ok"`，`pg`/`redis`/`mcp` 为 `"ok"|"degraded"`（见 `app/api/health.py`）。既有冒烟若 grep `healthy` 会假失败，须改对齐。
4. **degrade_* / rate_limiter**：批次9 服务层已有，**未挂**图节点 / API 主路径。
5. **EventPublisher 单真相**：仅 `app/events/publisher.py`；**不存在** `app/service/event_publisher.py`（已删，禁止复建）。
6. **冒烟**：仓库根 `scripts/smoke/smoke_complaint_service.sh`，默认端口 **8006**，须扩展勿新建。
7. **无 `.github/workflows/`**：本批新建。
8. **docker-compose.dev.yml** 可复用为压测基线。

## Goals / Non-Goals

**Goals:** 12.1～12.7 可跑；核心五模块覆盖率 ≥85%；E2E/冒烟在 `run_graph=true` 下可跑通图路径；11 降级服务层可断言；CI 可卡门禁；Prompt 回归流程可跑；压测 stub 基线可跑。

**Non-Goals:** 批次11 部署定稿；Skill；真 MQ；**图内挂载 degrade_***；rate_limiter 挂主路径；生产 SLA 级压测（PRD 20.1 P95≤15min）。

## Decisions

### D1: judge 最小接线（无 graph_service）

在 `complaint_service.judge` 实现：

- `run_graph=False`：保持现有 stub（兼容 batch8/9 单测与快速冒烟）。
- `run_graph=True`：调用 **`build_complaint_graph`**（MemorySaver/可注入 checkpointer）+ **`ainvoke`/`invoke`**，将图产出写入 session/verdict/事件；可选抽极薄 `app/graph/runner.py`（`async def run_complaint_graph(complaint_id, ...)`），**禁止** `graph_service.run_graph` 命名。

E2E 与「真图」冒烟项使用 `run_graph=true`；默认 stub 路径保留。

### D2: 测试分层与依赖切换

| 层级 | PG/Redis | LLM/MCP |
|---|---|---|
| unit | Fake/Mock | Mock |
| integration | 默认 Fake；`USE_REAL_INFRA=1` 可真 | Mock |
| e2e | 默认 Fake/TestClient | Mock + `run_graph=True` |
| regression | 不需要 | Mock LLM（固定 seed） |
| loadtest | docker-compose.dev.yml | stub / `run_graph=False` 为主 |

### D3: 冒烟扩展

扩展 `scripts/smoke/smoke_complaint_service.sh`（端口 **8006**）：

- health：`status=="ok"`；记录 `pg/redis/mcp`（可选：全为 ok 才 PASS，或显式打印 degraded）
- create / judge（可含 stub 与可选 run_graph）/ status / verdict / agent-logs
- mode：**PUT** `/api/v1/complaints/mode`，body 含 `signers:["ops","tech"]`
- 输出 `[PASS]`/`[FAIL]`；失败退出非 0

### D4: CI

新建 `.github/workflows/ci.yml`：unit + integration + 核心覆盖率门槛；冒烟可选（需起服务）；**若仓库已有** ruff/bandit/mypy 配置则启用，否则跳过并在 PR 清单注明。

### D5: Prompt 回归与 verdict 四值

golden_set 仅覆盖：

`agent_violation` | `user_misunderstanding` | `compliant_but_poor_experience` | `pending`

**禁止**孤立枚举值 `compliant`。起步 10～20 条；50+ 渐进：来源=脱敏历史/人工标注，JSON fixture，每迭代补样。

### D6: 压测指标 = stub 基线（非 PRD 生产 SLA）

本批准入（单实例 + stub/快速路径）：

- 接单 RPS ≥ 50
- judge P95 ≤ 3s（**stub 或轻量路径**）
- 查询 P95 ≤ 200ms
- 错误率 ≤ 1%

**明确**：≠ PRD 20.1「端到端 P95≤15min」生产基线；完整/多副本/真 LLM 压测 → 批次11 补强。

### D7: EventPublisher 单真相

测试断言只用 `from app.events.publisher import publisher`。  
**禁止**新建或恢复 `app/service/event_publisher.py`。

### D8: 覆盖率口径

- **门禁 A（必须）**：核心包/模块 `state`（含 reducer）、`graph/edges`、`recovery`/`fallback` 相关、`mcp`、`evidence_helper` — 合并覆盖率 ≥85%（可用 `--cov=app.models --cov=app.graph.edges --cov=app.mcp --cov=app.utils.evidence_helper --cov=app.service.recovery_service` 等精确列表，tasks 写死）。
- **门禁 B（可选报告）**：整包 `app` 覆盖率出报告，**本批不强制** `--cov-fail-under=85` 打在整包上（避免 stub/未接线模块拖死 CI）。

### D9: 11 降级测试触发面（禁止假装接线）

`tests/integration/test_degrade.py`：**直接调用** `DegradeLLM` / `DegradeMCP` / `DegradeFlow` 的 `handle_*`，断言 exception_info 码与 pending_review 等。  

**不要求**本批把 degrade 挂进 graph 节点。图路径上的 MCP 故障行为可另测节点自身 `exception_info`（既有节点 try/except），与批次9 degrade 服务层测试分离写清。

### D10: 人工驳回动作

回 `evidence_re_gather` 的路径使用 **`ReviewAction.reject_rejudge`**（不是仅 `modify_verdict`）。`modify_verdict` 用于改判/停止致电等既有行为。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| run_graph 真图不稳定 | E2E 专用；stub 默认保留；MemorySaver |
| 整包 85% 难达 | D8 只卡门核心五类 |
| 降级未进图 | D9 直调服务层；挂载列为后续 |
| 压测指标偏松 | D6 标明 stub 基线 |
| 冒烟端口/字段不一致 | D3 钉 8006 + 现网 health schema |

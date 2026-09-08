## Purpose

locust 压测与 stub 基线准入；非 PRD 生产 SLA。

## ADDED Requirements

### Requirement: locust 压测脚本

系统 SHALL 提供 `tests/loadtest/locustfile.py`：接单、judge（默认 stub 路径）、查询。

#### Scenario: 压测可跑

- **WHEN** 执行 locust
- **THEN** 产出 RPS、P95、错误率

### Requirement: 运行脚本与 stub 基线

系统 SHALL 提供 `run_loadtest.sh`，复用 `docker-compose.dev.yml`。

准入（**stub 基线**，非 PRD 20.1 生产 P95≤15min）：
- 接单 RPS ≥ 50
- judge P95 ≤ 3s（stub）
- 查询 P95 ≤ 200ms
- 错误率 ≤ 1%

低于指标 → 退出非 0。多副本/真 LLM → 批次11。

#### Scenario: 无批次11 时仍可跑基线

- **WHEN** 批次11 未完成
- **THEN** 单实例 compose 基线可执行

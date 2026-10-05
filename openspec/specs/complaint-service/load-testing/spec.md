## Purpose

定义投诉服务压测：locust 脚本与准入指标，复用 docker-compose 基线。

## Purpose

locust 压测与 stub 基线准入；非 PRD 生产 SLA。

## Requirements

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

### Requirement: 100 条洪峰批量与长稳重启压测

系统 SHALL 提供针对投诉研判事件入口的洪峰与长稳压测：(1) 容量批量——一次性注入 100 条 `complaint.created`，在候选并行度（4 → 8 → 16）下分别滚动消费，采集 LLM 429/熔断打开次数、PG checkpointer 连接池等待、HTTP/MCP 网关并发、单条端到端延迟、降级率，据此确定最终并行度；(2) 长稳——在最终并行度下持续注入流量保持 worker 满载不少于 30 分钟，并在窗口内主动触发一次服务重启/发布。压测 SHALL 输出逐项 pass/fail 判定与证据（日志、耗时、资源曲线），不以堆到 1000 条为目标。

#### Scenario: 100 条批量全部不丢不重

- **WHEN** 一次性注入 100 条投诉事件并在最终并行度下滚动消费至完成
- **THEN** 100 条全部最终到达人审/终态，完成数与注入数对账一致（不丢），每个 complaint_id 只产生一次有效研判（不重、无 checkpoint 分叉）

#### Scenario: 爬坡并行度指标定拐点

- **WHEN** 分别以并行度 4 / 8 / 16 各跑一批
- **THEN** 输出各档 LLM 429 与熔断次数、PG 连接池等待、网关并发、端到端延迟、降级率对照，作为最终并行度取值证据

#### Scenario: 长稳满载中途重启不丢不重

- **WHEN** 最终并行度满载运行 30 分钟，期间主动重启一次服务
- **THEN** 重启前后工单总数与完成数对账一致，未确认消息自动重投且被幂等/图锁拦截重复执行，无丢失、无重复研判

#### Scenario: 长稳资源无泄漏且异常不阻塞

- **WHEN** 30 分钟满载运行
- **THEN** 内存、TCP 连接、PG 连接、Redis key 不随时间持续增长（三表裁剪生效）；超时/异常工单正确转 pending_review 且不阻塞队列滚动；降级率与端到端延迟不随时间劣化

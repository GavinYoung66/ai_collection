## Purpose

为 qc-service 离线开检提供有界并发闸，限制同时占用 LLM/检索的路数，超过上限的请求排队等槽，避免与投诉研判争抢模型容量。

## ADDED Requirements

### Requirement: 离线开检有界并发且超额排队

离线质检执行（含 replay / raw / batch 内部开检）SHALL 受有界并发闸约束。最大同时在途路数 `max_inflight` SHALL 从 `QC_MAX_INFLIGHT` 读取，默认 3，MUST 为正整数。在途数未达上限时 SHALL 立即开检；已达上限时后续开检 MUST 排队等待槽位释放后再执行，MUST NOT 丢弃请求，MUST NOT 因排队本身返回 500 或把 `qc_status` 置为 PARTIAL。在途数 MUST NOT 超过 `max_inflight`。

等待超时 `QC_INFLIGHT_WAIT_TIMEOUT` SHALL 默认 0，表示无限等待（本变更 MUST NOT 把等待超时变成 HTTP 500，也 MUST NOT 在未获槽时超 `max_inflight` 硬跑）。成功 `acquire` 之后 MUST 在 `try/finally` 中 `release()`；取消或异常 MUST 同样释放，禁止槽位泄漏。

#### Scenario: 超过 max_inflight 排队而不超并发

- **WHEN** 已有 `max_inflight` 路离线开检在执行，再提交一路开检
- **THEN** 新请求等待直到有槽位，同时在途开检数不超过 `max_inflight`

#### Scenario: 默认并发为 3

- **WHEN** 未配置覆盖值
- **THEN** `max_inflight` 为 3

#### Scenario: 异常退出仍释放槽位

- **WHEN** 已获得槽位的开检在执行中被取消或抛异常
- **THEN** 槽位被释放，后续等待者可以进入，在途数回落

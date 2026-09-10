## Purpose

定义 qc-service M1 验收：可执行 DD-02 §8.2 断言 1–28、不起 call-service 的冒烟、镜像/环境，以及明确不编码 M2+ RAG 检索与红线 6 护栏日志。

## ADDED Requirements

### Requirement: 引擎单测覆盖合并与 skip 核心

引擎单测 SHALL 在 `MOCK_LLM=1` 下运行，MUST 覆盖断言 6、19、20、22–25、27（AC 窗口、MergeHits、R04/R10/R14 skip、用户不计分）。

#### Scenario: 引擎测试存在

- **WHEN** 运行 `services/qc-service/tests/test_engine*.py`
- **THEN** 断言 6、19、20、22–25、27 在 MOCK_LLM=1 下通过

### Requirement: 断言 1–28 可执行

系统 SHALL 将 DD-02 §8.2 断言 1–28 做成可执行用例。QC-11.2 仍是一个任务 ID：轮次 3.2 SHALL 覆盖断言 1–14；轮次 3.3 SHALL 覆盖断言 15–28。断言 5 SHALL 订阅 `qc.escalate.human` 且无消息。断言 23/24 SHALL 断言零次 rag `/kb/search` 调用。断言 26 MAY 使用 Mock DP。断言 28 SHALL DEL Redis 后从 audit_logs GET 回 `skipped_rules`。

| # | SHALL |
| :--- | :--- |
| 1 | raw replay 含坐席高危话术且 `call_ended=true` → score≤60，risk=high，grade=SERIOUS。夹具 SHALL 含足够多可计分的 agent-high hits 使 `compliance_score≤60`（MUST NOT 用单条 −20，那是 80/PASS/high） |
| 2 | `qc_records` high_count=1 |
| 3 | GET `/qc/alerts` 返回 1 条 NEW 高危告警 |
| 4 | PSUBSCRIBE `qc.alert.raised` 收到该告警 |
| 5 | 订阅 `qc.escalate.human` → 无消息 |
| 6 | 同词 20s 内两次 → 30s 去重，100-2=98 |
| 7 | 23:30 通话 → R02 命中，−8 |
| 8 | 用户停呼后坐席继续 → R13 + `qc.stop_call.detected`；用户行不计分 |
| 9 | 「我是法院的」→ R12 fake_identity high |
| 10 | POST reviews CORRECT → 分数更新，`qc_status=REVIEWED` |
| 11 | POST replay `{session_id}` 从 DP 种子成功 |
| 12 | LLM mock 熔断 → 仍出 AC+规则分 |
| 13 | evidence_text 无完整手机号/身份证 |
| 14 | `call_ended=false` 或空 turns → A2002，无 DONE 行 |
| 15 | 用户自杀 + 坐席安抚、未再催收 → 不扣坐席分，不发 suspected |
| 16 | 用户提及投诉后坐席仍催收 → R13，扣坐席分 |
| 17 | 未结束会话 replay → A2003，Redis WAIT_TEXT，无 `qc_records` |
| 18 | raw replay 无 `call_records` → A2003，不插表 |
| 19 | 同轮 AC threat + R11 → 只扣 20；1 条高危告警 |
| 20 | 无时间戳、turn_index 差 1 → 第二条 AC 丢弃 |
| 21 | 用户自杀 + 坐席安抚（无 R13）→ 无 qc_alerts，不发 suspected |
| 22 | 仅 R05 → 扣 2，无告警，不发 suspected |
| 23 | R14 无关键词且无 hit_text → skipped_rules 含 R14；无 `/kb/search`；仅因 skip R14 MUST NOT 把 `qc_status` 置 PARTIAL，允许为 DONE |
| 24 | R14 关键词或 hit_text 命中 → R14 hit；仍无 RAG 检索 HTTP |
| 25 | `result=completed` + agent threat → R10 命中；无 result → skip R10，不填 unverified |
| 26 | qc-source 有 `result` 键（可空）；缺 dial_history → skip R05；`ptp_amount` null → skip R08 |
| 27 | R04：身份确认前披露命中；「是我」之后不命中 |
| 28 | 写入 skipped_rules=["R14"]，DEL Redis job，GET 仍返回该数组 |

#### Scenario: 轮次 3.2 覆盖 1–14

- **WHEN** 执行验收轮次 3.2
- **THEN** 断言 1–14（含无 `qc.escalate.human` 消息）可执行且通过

#### Scenario: 断言 1 夹具不是单条 −20

- **WHEN** 跑断言 1 raw replay
- **THEN** `compliance_score≤60`、`risk=high`、`grade=SERIOUS`（夹具有足够 agent-high；MUST NOT 用单独 −20）

#### Scenario: 轮次 3.3 覆盖 15–28

- **WHEN** 执行验收轮次 3.3
- **THEN** 断言 15–28（含 23/24 无 `/kb/search`）可执行且通过

### Requirement: 冒烟不起 call-service

`scripts/smoke/smoke_qc.sh` SHALL 覆盖 health → 种子通话 → replay/raw → records/alerts。依赖 SHALL 为 rag + DP + PG + Redis。冒烟 MUST NOT 启动 call-service。

#### Scenario: 冒烟路径

- **WHEN** 对已种子数据跑 smoke_qc.sh
- **THEN** health、replay/raw、records/alerts 成功，且无 call-service 进程

### Requirement: 跳过 M2+ 测试

测试 MUST 跳过 RAG 标准答案检索、红线 6 护栏放行日志、实时 WS。测试 MUST NOT 要求 `/kb/search` 或 `guardrail.pass`。红线 6 不是 M1 必过项。

#### Scenario: 不测 kb search

- **WHEN** 收集 M1 测试集
- **THEN** 会调用 `/kb/search` 或断言 `guardrail.pass` 的用例被 skip 或不存在

### Requirement: 镜像与环境

Dockerfile SHALL 暴露 8003，HEALTHCHECK `/health`，启动时拉词库。Compose/env SHALL 注入 DD-02 §12，含 Redis DB 3 与 `QC_R14_RAG_ENABLED=false`。MUST NOT 增加实时质检超时配置项。

#### Scenario: Healthcheck 用根路径

- **WHEN** 构建镜像
- **THEN** HEALTHCHECK 探测 `GET /health`，进程监听 8003

### Requirement: QC-13.2 与 QC-13.3 不编码

QC-13.2（R14 `POST /api/v1/kb/search`）与 QC-13.3（call `audit_logs.action=guardrail.pass`）SHALL 登记为 M2+，MUST NOT 占用 apply 轮次。M1 MUST 保持 `QC_R14_RAG_ENABLED=false`。缺护栏日志仍 MUST NOT 标 `unverified`。

#### Scenario: 本 change 无 M2+ 实现

- **WHEN** 本 change 全部 apply 完成
- **THEN** qc-service 中无 `/kb/search` 客户端、无 guardrail.pass 读取器

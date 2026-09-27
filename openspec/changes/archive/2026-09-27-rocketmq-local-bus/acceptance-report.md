# Acceptance Report — `rocketmq-local-bus` (§7)

**Date:** 2026-09-27  
**Mode:** spike = **go** → 验收以 `EVENT_BUS=rocketmq` 为主路径；`EVENT_BUS=redis` 作灰度对照。  
**Verifier:** Batch 4 apply (`7.1`–`7.5`) + 非阻断加固  
**Min-loop note:** `run_min_loop.py` 输出含 `6.1`/`6.2`…`6.11` 及子项 `6.1-judge`/`6.5-pre`，表计 **13/13**；对应 tasks 所述 **11/11** 主项全部 PASS。

**证据目录（耐久副本）：** `openspec/changes/rocketmq-local-bus/spike-evidence/acceptance-logs/`  
（原 `/tmp/*.log` 已拷贝；若本地 `/tmp` 已清，以本目录或脚本重跑为准，见同目录 `README.md`。）

## 逐项对照（§7）

| 项 | 结果 | 模式 / 命令 | 证据摘要 |
|---|---|---|---|
| **7.1** redis 回归 | **PASS** | `EVENT_BUS=redis` · `run_min_loop.py` | `acceptance-logs/min_loop_redis_7_1_v3.log` → `总计: 13/13`；`transport=redis_pubsub` |
| **7.2** rocketmq 回归 | **PASS** | `EVENT_BUS=rocketmq` · `ROCKETMQ_PROXY=localhost:18081` | `acceptance-logs/min_loop_rocketmq_7_2_clean.log` → `总计: 13/13`；干净路径非幂等跳过 |
| **7.3** DLQ 闭环 | **PASS** | fail → retry×4 → `%DLQ%` → replay | `acceptance-logs/acceptance_7_3_dlq.log`；`evt_accept_dlq_0fc8db59` |
| **7.4** 信封强校验 | **PASS** | 发布拒绝 + Topic 直投消费拒绝 | `acceptance-logs/acceptance_7_4_envelope.log`；双侧拒绝日志 |
| **7.5** 本报告 | **PASS** | 本文件 | `openspec/changes/rocketmq-local-bus/acceptance-report.md` |

---

### 7.1 证据（redis）

```text
# 命令
EVENT_BUS=redis EVENT_BUS_SVC=dp PYTHONPATH=services/data-platform:. \
  python services/data-platform/scripts/run_min_loop.py

# 关键片段（acceptance-logs/min_loop_redis_7_1_v3.log）
eventbus_publish ... event_name=etl.task.done ... transport=redis_pubsub
总计: 13/13 通过
```

### 7.2 证据（rocketmq，与 redis 行为一致）

```text
# 命令
EVENT_BUS=rocketmq EVENT_BUS_SVC=dp ROCKETMQ_PROXY=localhost:18081 \
  PYTHONPATH=services/data-platform:. \
  python services/data-platform/scripts/run_min_loop.py

# 关键片段（acceptance-logs/min_loop_rocketmq_7_2_clean.log）
eventbus_subscribe ... transport=rocketmq topic=csmp_business_events
eventbus_publish ... etl.task.done ... message_id=014646813919FA54B30ACA9F1D00000000
GET http://localhost:8003/api/v1/qc/records/min-loop-sess-001  → score/risk 反拉
[6.4] ... after_publish=True idempotent_rerun=False
总计: 13/13 通过
```

与 7.1 对照：同套 6.x 验收项全绿；传输层为 `rocketmq` / Topic `csmp_business_events`。

### 7.3 证据（DLQ 闭环）

脚本：`spike-evidence/accept_7_3_dlq.py` · 日志：`acceptance-logs/acceptance_7_3_dlq.log`

| 步骤 | 结果 | 证据 |
|---|---|---|
| 人为失败 | PASS | `HANDLER_FAIL` / `accept_dlq_forced_failure` |
| 重试 3 次（共 4 次） | PASS | `attempt=1..4 max_attempt=4` |
| 入 DLQ + 告警字段 | PASS | `csmp.eventbus.dlq` 含 `event_id`/`event_name`/`consumer_group`/`trace_id`/`last_error`/`dlq_timestamp` |
| dry-run / replay | PASS | `matched=1 dry_run=True` → `REPLAYED ... dry_run=False` |

### 7.4 证据（信封强校验）

脚本：`spike-evidence/accept_7_4_envelope.py` · 日志：`acceptance-logs/acceptance_7_4_envelope.log`（已加固后重跑）

- 发布端：`event_envelope_data_rejected`（`rejected_field=conversation_text`/`score`）+ `BUS_PUBLISH_REJECT`
- 消费端：同事件 `event_envelope_data_rejected` + `eventbus_consume_reject` **同条日志含** `event_id` / `trace_id` / `rejected_field`

```text
eventbus_consume_reject ... event_id=evt_accept_cns_34f2e17767
  trace_id=tr_accept_cns_16270a2d rejected_field=conversation_text
  rejected_fields=['conversation_text', 'score'] transport=rocketmq
PASS 7.4 ... bus_publish_rejected=True live_consume_reject=True
```

---

## Spec / Design 关键 MUST 对照（不仅 §7 标题）

| MUST | Spec / Design | 实现 | 验收证据 | 结论 |
|---|---|---|---|---|
| 信封白名单双侧强校验 | `platform/event-bus`；`qc-service/events` | `envelope.py` + 三适配器 | 7.4 日志；`pytest common/eventbus/tests/test_envelope.py` | PASS |
| Topic=`csmp_business_events` + Tag | `platform/event-bus`；`min-loop` | `PHYSICAL_TOPIC`；rocketmq tag | 7.2/7.3 publish 日志 | PASS |
| 消费组 `gid_{svc}_{event}` | `platform/event-bus` | `base.consumer_group_name` | 7.2 `gid_dp_min_loop_qc_check_completed`；业务 `gid_qc_*` | PASS |
| RocketMQ 重试 3→DLQ→告警→重放 | `platform/event-bus` | `rocketmq.py` + `event_replay.py` | 7.3 逐步日志 | PASS |
| Redis **无**持久化 DLQ | `platform/event-bus` Decision 5 | `redis_pubsub.py` 仅进程内重试 | 代码路径 + tasks 5.1；§7 未再测 redis DLQ（N/A） | PASS |
| hooks `csmp.eventbus.*` | `platform/event-bus` 可观测 | `observability.py` | 7.2/7.3 hook 日志 | PASS |
| `EVENT_BUS=foo` → `ValueError` | `platform/event-bus` 环境切换 | `factory.py` | `pytest common/eventbus/tests/test_factory.py` | PASS |
| SHALL NOT 双写 | Non-Goals；event-bus | factory 单适配器 | 代码无 dual-write | PASS |
| 事件仅 ID；HTTP 反拉详情 | `integration-boundaries`；`min-loop` | QC `whitelist_data`；DP `QcClient` | 7.2：`GET .../qc/records/...` → score=72 | PASS |
| `qc.check.completed` 不自动建投诉 | `min-loop` | DP subscriber 仅回流质检列 | 代码无 POST complaints；投诉在 min_loop 显式步骤 | PASS |
| 幂等消费（至少一次） | `platform/event-bus` 幂等 | 信封暴露 `event_id`；订阅方可去重 | `pytest common/eventbus/tests/test_idempotent_consume.py`；7.2 `idempotent_rerun` 探测 | PASS |
| 不做投诉链事件化 / 保留 Redis Pub/Sub | Non-Goals | `redis_pubsub.py` 保留 | 无 `complaint.created` 落地；7.1 走 redis | PASS |
| QC 其余 M1 出站仍 Redis（非漏迁） | proposal 三事件范围；Non-Goals | qc `publisher._BUS_CHANNELS` 仅 `qc.check.completed` | 模块 docstring + 本表；alert/review 等仍 Redis | PASS |
| spike go → rocketmq 适配器 | design D6；spike-report | `rocketmq.py` | spike **go**；7.2 rocketmq | PASS |
| lock/otlp 与 rocketmq 冲突已文档化 | tasks 3.3 / 运维 | Dockerfile 分步装；lock/txt 注释 | `requirements.lock` / `requirements.txt` / Dockerfile 注释 | PASS |

---

## 运行时硬化（非阻断项落地）

1. **证据入库**：日志已拷至 `spike-evidence/acceptance-logs/`；7.4 已重跑，`eventbus_consume_reject` 行含 `event_id`/`rejected_field`。  
2. **镜像烘焙**：`requirements.lock` 已 pin `rocketmq-python-client==5.1.1`。qc/data-platform Dockerfile 分步安装（先 lock 排除 rocketmq，再装 rocketmq + `opentelemetry-exporter-otlp>=1.33.1`，规避与 langfuse 的 otlp 小版本冲突），构建时 `import rocketmq` 校验。重建后容器内无需临时 `pip install`：
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml build qc-service data-platform
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d qc-service data-platform
   docker exec ai_collection-qc-service-1 python -c "import rocketmq"
   ```
   **勿**对整仓 lock 做一次 `pip install -r requirements.lock` 期望同时装上 rocketmq+langfuse（见 lock/txt 注释）。  
3. **Topic message.type**：`rocketmq-init-topics` 创建 Topic 时附加 `-a +message.type=NORMAL`。  
4. **consume_reject 字段**：三适配器 `eventbus_consume_reject` 现携带 `event_id` / `trace_id` / `rejected_field`（`peek_reject_meta`）。  
5. **三事件范围**：仅 `call.session.ended` / `etl.task.done` / `qc.check.completed` 上总线；qc 其余 M1 出站仍 Redis——有意保留，非漏迁。  
6. **幂等**：`test_idempotent_consume.py` 覆盖同 `event_id` 重投只处理一次的订阅方模式。

---

## 阻断问题清单

无

---

## 复核指引

1. 对照 `spike-evidence/acceptance-logs/` 或按该目录 `README.md` 重跑。  
2. `tasks.md` 中 `1.x`–`7.x` 应为 25/25 `[x]`。  
3. Archive：交叉验证通过后 `/opsx-archive rocketmq-local-bus`。

# Acceptance logs (§7)

Durable copies of Batch-4 acceptance runs (originally under `/tmp`).

| File | Maps to |
|------|---------|
| `min_loop_redis_7_1_v3.log` | task 7.1 |
| `min_loop_rocketmq_7_2_clean.log` | task 7.2 |
| `acceptance_7_3_dlq.log` | task 7.3 |
| `acceptance_7_4_envelope.log` | task 7.4 |

Re-run (source of truth if logs rotate):

```bash
# 7.1
EVENT_BUS=redis EVENT_BUS_SVC=dp PYTHONPATH=services/data-platform:. \
  python services/data-platform/scripts/run_min_loop.py

# 7.2
EVENT_BUS=rocketmq EVENT_BUS_SVC=dp ROCKETMQ_PROXY=localhost:18081 \
  PYTHONPATH=services/data-platform:. \
  python services/data-platform/scripts/run_min_loop.py

# 7.3 / 7.4
PYTHONPATH=. ROCKETMQ_PROXY=localhost:18081 \
  python openspec/changes/rocketmq-local-bus/spike-evidence/accept_7_3_dlq.py
PYTHONPATH=. ROCKETMQ_PROXY=localhost:18081 \
  python openspec/changes/rocketmq-local-bus/spike-evidence/accept_7_4_envelope.py
```

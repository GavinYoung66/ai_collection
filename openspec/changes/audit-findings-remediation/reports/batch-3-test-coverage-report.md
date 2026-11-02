# 批次 3 测试覆盖报告：消息可靠性与资源泄漏（T09–T12）

- 日期：2026-11-02
- 测试框架：pytest 7.4.4 + pytest-asyncio（`asyncio_mode=auto`）
- 运行环境：`/tmp/ai_collection_test_venv`，Python 3.11.15
  （在 conda `ai_collection` 环境基础上补齐 `prometheus_client`、
  `structlog`、`rocketmq-python-client==5.1.1` 测试依赖）

## 1. 覆盖汇总

| 任务 | 测试文件 | 用例数 | 正常 | 边界 | 异常 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| T09 | `common/eventbus/tests/test_t09_dlq_replay.py` | 6 | 1 | 2 | 3 | 6 passed |
| T09 | `common/eventbus/tests/test_rocketmq_dlq.py` | 3（新增 1） | 1 | 0 | 2 | 3 passed |
| T10 | `services/complaint-service/tests/unit/test_t10_complaint_persist_idempotent.py` | 5 | 1 | 2 | 2 | 5 passed |
| T10 | `services/complaint-service/tests/integration/test_session_persistence.py` | +1（PG） | 1 | 0 | 0 | 1 skipped（需 PG） |
| T11 | `services/rag-service/tests/test_t11_asr_segment_fd.py` | 5 | 1 | 2 | 2 | 5 passed |
| T12 | `common/eventbus/tests/test_t12_subscribe_loop_resilience.py` | 3 | 1 | 1 | 1 | 3 passed |
| T12 | `common/eventbus/tests/test_rocketmq.py` | 5（改写 1） | 2 | 0 | 1 | 5 passed |

批次新增/更新用例合计 22 个（上表中 T09 的 `test_rocketmq_dlq.py` 与 T12 的
`test_rocketmq.py` 为既有文件，分别新增 1 个、改写 1 个用例）：本地实跑
21 passed，1 个真实 PG 并发用例因缺 PostgreSQL 环境跳过。

## 2. T09 —— DLQ 重放与生产失败不 ACK

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_dlq_produce_failure_does_not_ack` | 异常 | `_publish_sync` 抛异常 → `consumer.ack` 未被调用，且 `csmp.eventbus.dlq` hook 仍在 | ✅ |
| `test_replay_by_event_id_hits_exact_message` | 正常 | 只重投 event_id 命中的消息，未命中条目 skipped | ✅ |
| `test_replay_by_time_window_filters_outside_messages` | 边界 | `since/until` 窗口外消息 skipped，窗口内重投 | ✅ |
| `test_replay_dry_run_does_not_publish_but_acks` | 边界 | 匹配但不重投，DLQ 消息 ACK | ✅ |
| `test_replay_publish_failure_does_not_ack` | 异常 | 重投失败 → failed 计数 +1，原 DLQ 消息不 ACK | ✅ |
| `test_replay_receive_error_soft_exits_and_shuts_down` | 异常 | receive 异常软退出并 `shutdown` | ✅ |
| `test_replay_requires_criteria_and_valid_window` | 异常 | 无条件 / `since > until` → `ValueError` | ✅ |

## 3. T10 —— 工单落库原子幂等

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_insert_uses_on_conflict_do_nothing` | 正常 | 语句含 `on_conflict_do_nothing`，`id` 为冲突键，`rowcount=1` 后 commit | ✅ |
| `test_conflict_with_active_row_is_idempotent_success` | 边界 | 冲突且行为活跃 → 不重复写、不抛错 | ✅ |
| `test_conflict_with_soft_deleted_row_is_restored` | 边界 | 软删行恢复为 `is_delete="0"` 并刷新文本/状态 | ✅ |
| `test_invalid_uuid_raises_without_touching_db` | 异常 | 非法 UUID → `ComplaintPersistError`，不访问 DB | ✅ |
| `test_db_error_rolls_back_and_wraps` | 异常 | DB 异常 → rollback 且包装为 `ComplaintPersistError` | ✅ |
| `test_concurrent_persist_same_case_id_all_succeed` | 正常（PG） | 两个并发落库均成功，`complaints` 仅 1 行 | ⏭️ skip |

## 4. T11 —— ASR 分段 fd

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_segment_audio_returns_all_bytes_in_order` | 正常 | 3 段按序返回，临时目录被清理 | ✅ |
| `test_segment_audio_does_not_use_unclosed_binary_open` | 边界/资源 | spy `builtins.open`，不存在 `"r"` 模式读取调用 | ✅ |
| `test_segment_audio_fd_count_stays_flat_across_batches` | 边界 | 连续 100 段后 fd 增量 ≤ 5 | ✅ |
| `test_segment_audio_without_ffmpeg_raises` | 异常 | ffmpeg 不可用 → `RuntimeError` | ✅ |
| `test_segment_audio_with_no_output_raises` | 边界/异常 | 无片段输出 → `RuntimeError`，不返回空列表 | ✅ |

## 5. T12 —— 订阅循环韧性

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_receive_error_does_not_stop_subscription` | 异常 | 首次 receive 抛错后循环存活，后续消息被消费并 ACK | ✅ |
| `test_receive_recovery_logged_after_consecutive_failures` | 正常 | 连续 2 次失败后恢复，记录 `eventbus_consumer_receive_recovered` | ✅ |
| `test_receive_backoff_is_exponential_and_capped` | 边界 | 1→1s、2→2s、5→16s、6/100→30s | ✅ |
| `test_receive_failure_emits_warning_and_continues` | 异常（既有用例改写） | warning 含 error/consumer_group，循环不退出 | ✅ |

## 6. 回归结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 事件总线全量 | `pytest common/eventbus/tests -q` | **54 passed** |
| RAG 服务全量 | `pytest services/rag-service/tests -q` | **380 passed, 1 skipped** |
| 工单服务单元全量 | `pytest services/complaint-service/tests/unit -q` | **358 passed, 4 failed, 5 errors**（失败均为既有环境问题，见 §7） |
| 工单服务集成 | `pytest services/complaint-service/tests/integration/test_session_persistence.py -q` | **2 passed, 12 skipped** |
| T05/T06/T10 聚焦 | 三个文件单独运行 | **16 passed** |
| T11 + ASR 客户端聚焦 | 两个文件单独运行 | **7 passed** |

## 7. 失败项归因（均为既有问题，与 T09–T12 无关）

| 失败项 | 现象 | 归因 |
| --- | --- | --- |
| `test_mcp_real_path.py`（1 failed + 5 errors） | `PydanticUserError: A non-annotated attribute` | 本地 pydantic 2.5 与 MCP SDK 版本不兼容；CI 依赖矩阵下不受影响 |
| `test_t07_publisher_task_refs.py`（整套运行时 3 failed） | `pending_bus_publish_tasks()` 残留 67 | 进程级全局任务集合被其他用例污染；单独运行 3 passed，属既有测试隔离问题 |

> 上述失败项在本次改动前即存在，T09–T12 未修改对应实现。

## 8. 静态检查

| 检查 | 范围 | 结果 |
| --- | --- | --- |
| `ruff check` | 本次改动文件（实现 + 测试） | 零告警 |
| `python -m compileall` | 本次改动文件 | 通过，无编译错误 |

## 9. 复现命令

```bash
# 批次 3 相关用例（按服务隔离运行，避免多服务 conftest 冲突）
/tmp/ai_collection_test_venv/bin/python -m pytest \
  common/eventbus/tests/test_t09_dlq_replay.py \
  common/eventbus/tests/test_rocketmq_dlq.py \
  common/eventbus/tests/test_t12_subscribe_loop_resilience.py -q

/tmp/ai_collection_test_venv/bin/python -m pytest \
  services/complaint-service/tests/unit/test_t10_complaint_persist_idempotent.py -q

/tmp/ai_collection_test_venv/bin/python -m pytest \
  services/rag-service/tests/test_t11_asr_segment_fd.py -q

# 真实 PG 并发验收（需 PostgreSQL）
COMPLAINT_DB_TESTS=1 /tmp/ai_collection_test_venv/bin/python -m pytest \
  services/complaint-service/tests/integration/test_session_persistence.py -q
```

## 10. 覆盖缺口与说明

1. **真实 RocketMQ Broker 链路**未在本地验证：DLQ 投递、不可见超时重投、
   跨进程重放均为 mock 或脚本化 consumer 覆盖；需在集成环境补一次端到端演练。
2. **真实 PostgreSQL 并发**用例已编写但本地 skip，需 `COMPLAINT_DB_TESTS=1` 环境执行。
3. **fd 水位**断言在 macOS 使用 `/dev/fd`；Linux CI 使用 `/proc/self/fd`，逻辑一致。

# 批次 3 开发完成报告：消息可靠性与资源泄漏（T09–T12）

- 日期：2026-11-02
- 批次：`3. 🟡 消息可靠性与资源泄漏`
- 依据：`openspec/changes/audit-findings-remediation/{proposal,design,tasks}.md`、
  `specs/platform/event-bus/spec.md`、`specs/platform/runtime-correctness/spec.md`
- 执行顺序：T09 → T10 → T11 → T12（严格按编号，未跳项）

## 1. 总体完成情况

| 编号 | 问题 | 状态 | 核心文件 | 单测 |
| --- | --- | --- | --- | --- |
| T09 | DLQ 无人消费 + DLQ 生产失败仍 ACK | ✅ 完成 | `common/eventbus/rocketmq.py` | 9 passed |
| T10 | 工单落库幂等非原子（SELECT-then-INSERT） | ✅ 完成 | `services/complaint-service/app/service/complaint_service.py` | 5 passed（+1 PG 并发用例待环境） |
| T11 | ASR 分段读取不关闭句柄，fd 泄漏 | ✅ 完成 | `services/rag-service/app/services/asr_providers/dashscope_provider.py` | 5 passed |
| T12 | receive 异常冲出循环，订阅永久停止 | ✅ 完成 | `common/eventbus/rocketmq.py` | 4 passed |

静态检查：`ruff check` 针对本次改动文件零告警；`python -m compileall` 全部通过，无编译错误。

## 2. T09 —— DLQ 重放与「生产失败不 ACK」

### 实现要点

1. `_send_to_dlq` 返回值由 `None` 改为 `bool`：
   - 成功投递 `%DLQ%{consumer_group}` → `True`
   - 任何异常 → 记录 `eventbus_dlq_produce_failed` 并返回 `False`
2. `_handle_message` 重试耗尽后：
   - `dlq_produced = await self._send_to_dlq(...)`
   - `True`：ACK 原消息，完成至少一次语义闭环
   - `False`：记录 `eventbus_dlq_produce_failed_no_ack` 并 **直接 return，不 ACK**；
     由 Broker 不可见超时重投，下次重投继续走完整重试并再次尝试入 DLQ，
     避免「确认即丢失」
3. 新增运维重放入口 `replay_dlq()`：
   - 精确模式：`event_id`
   - 窗口模式：`since` / `until`（ISO8601，naive 按 UTC）
   - 无 `event_id` 且无窗口 → `ValueError`
   - `since > until` → `ValueError`
   - `dry_run=True` 只扫描匹配并 ACK，不重新投递
   - `max_messages` 限制单次扫描条数
   - 命中后先缓存 `_dlq` 元数据（取 `original_topic`），移除 `_dlq` 再重投原 Topic
   - 重投失败不 ACK，留待下一次重放
   - 返回统计：`scanned / matched / replayed / skipped / failed`
4. 新增辅助函数 `_parse_ts / _dlq_event_time / _in_window / _decode_dlq_body`
   与 `_start_replay_consumer`（绑定 `%DLQ%` Topic）。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| mock DLQ 写入失败时断言未 ACK | ✅ `test_dlq_produce_failure_does_not_ack` |
| 重放入口按 `event_id` 命中 | ✅ `test_replay_by_event_id_hits_exact_message` |
| 时间窗口过滤 | ✅ `test_replay_by_time_window_filters_outside_messages` |
| dry-run 不重投但 ACK | ✅ `test_replay_dry_run_does_not_publish_but_acks` |
| 重投失败不 ACK | ✅ `test_replay_publish_failure_does_not_ack` |

## 3. T10 —— 工单落库原子幂等

### 实现要点

`_persist_complaint_row` 由「SELECT 再 INSERT」改为单语句原子写入：

```python
stmt = (
    pg_insert(Complaint)
    .values(id=case_uuid, case_id=case_uuid,
            complaint_text=complaint_text, status="pending")
    .on_conflict_do_nothing(index_elements=[Complaint.id])
)
result = await db.execute(stmt)
```

- `rowcount == 1`：新插入成功，`commit` 后返回
- 冲突命中：查询既有行
  - `is_delete == "1"` 的软删行 → 恢复为活跃行（`is_delete="0"`、刷新文本与状态）
  - 活跃行 → 记录 `complaint_persist_idempotent_hit`，按幂等成功返回
- 非 UUID 的 `case_id` → `ComplaintPersistError`
- DB 异常 → `rollback` 后包装为 `ComplaintPersistError`，禁止静默成功

并发下后到请求不再触发主键冲突，两个请求都返回成功，数据库仅一条记录。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 并发两请求均返回成功、库中仅一条 | ✅ 单测覆盖幂等路径；真实 PG 并发用例已补充 |
| 正常 INSERT 使用 ON CONFLICT | ✅ `test_insert_uses_on_conflict_do_nothing` |
| 冲突活跃行幂等成功 | ✅ `test_conflict_with_active_row_is_idempotent_success` |
| 软删行恢复 | ✅ `test_conflict_with_soft_deleted_row_is_restored` |
| 非法 UUID 明确失败 | ✅ `test_invalid_uuid_raises_without_touching_db` |
| DB 异常回滚并包装 | ✅ `test_db_error_rolls_back_and_wraps` |

> 真实 PG 并发验收用例 `test_concurrent_persist_same_case_id_all_succeed`
> 需 `COMPLAINT_DB_TESTS=1` 且 PostgreSQL 可用；本地环境未启动 PG，自动 skip。

## 4. T11 —— ASR 分段读取确定性关闭句柄

### 实现要点

```python
return [Path(p).read_bytes() for p in segs]
```

`Path.read_bytes()` 在返回前确定性关闭文件句柄，不依赖 GC 回收；
避免长音频批量分段时 fd 累积。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 连续处理 100 段后 fd 数不线性增长 | ✅ `test_segment_audio_fd_count_stays_flat_across_batches` |
| 不再出现裸 `open(p, 'rb')` 读取 | ✅ `test_segment_audio_does_not_use_unclosed_binary_open` |
| 片段顺序与字节内容正确 | ✅ `test_segment_audio_returns_all_bytes_in_order` |
| ffmpeg 缺失 / 无输出边界 | ✅ 两个异常用例 |

## 5. T12 —— 订阅循环韧性（receive 异常不退出）

### 实现要点

`_consume_loop` 对 `receive` 异常由 `raise` 改为「退避 + continue」：

- 连续失败计数 `receive_failures`
- `_receive_backoff_seconds(failures) = min(2^(failures-1), 30)`：1s 起、30s 封顶
- 每次失败记录 `eventbus_consumer_receive_failed`，带
  `consecutive_failures`、`retry_in_seconds`、`error_type`
- `await asyncio.sleep(delay)` 后 `continue`
- 恢复后记录 `eventbus_consumer_receive_recovered` 并清零计数
- `asyncio.CancelledError` 仍正常上抛，保证优雅停机

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 注入抛异常后，后续消息仍被消费 | ✅ `test_receive_error_does_not_stop_subscription` |
| 连续失败后恢复可观测 | ✅ `test_receive_recovery_logged_after_consecutive_failures` |
| 退避指数与封顶边界 | ✅ `test_receive_backoff_is_exponential_and_capped` |
| 既有契约同步 | ✅ `test_rocketmq.py::test_receive_failure_emits_warning_and_continues`（原「抛异常」断言已按新规范改写） |

## 6. 变更文件清单

| 类型 | 文件 |
| --- | --- |
| 实现 | `common/eventbus/rocketmq.py` |
| 实现 | `services/complaint-service/app/service/complaint_service.py` |
| 实现 | `services/rag-service/app/services/asr_providers/dashscope_provider.py` |
| 测试 | `common/eventbus/tests/test_t09_dlq_replay.py`（新增） |
| 测试 | `common/eventbus/tests/test_t12_subscribe_loop_resilience.py`（新增） |
| 测试 | `common/eventbus/tests/test_rocketmq_dlq.py`（新增失败不 ACK 用例） |
| 测试 | `common/eventbus/tests/test_rocketmq.py`（按 T12 新契约改写 1 个用例） |
| 测试 | `services/complaint-service/tests/unit/test_t10_complaint_persist_idempotent.py`（新增） |
| 测试 | `services/complaint-service/tests/integration/test_session_persistence.py`（新增 PG 并发用例） |
| 测试 | `services/rag-service/tests/test_t11_asr_segment_fd.py`（新增） |
| 规范 | `openspec/changes/audit-findings-remediation/tasks.md`（T09–T12 勾选与状态回写） |

## 7. 顺带修复（运行前置）

- `complaint_service.py` 补充 `LOCK_UNAVAILABLE` 导入：此前缺失导致既有 T06
  单测 `NameError`，属运行 T10 相关用例的必要前置修复。
- 移除 `except TimeoutError as e` 中未使用的局部变量，保证本次改动文件通过静态检查。

## 8. 环境说明与遗留

- 本地回归使用 `/tmp/ai_collection_test_venv`（Python 3.11.15，补齐
  `prometheus_client`、`structlog`、`rocketmq-python-client==5.1.1`）。
- `test_mcp_real_path.py` 的 5 个 error + 1 个 failure 为本地 pydantic 2.5 与 MCP SDK
  的既有兼容问题，与 T09–T12 无关。
- `test_t07_publisher_task_refs.py` 在整套 unit 目录运行时受跨用例全局任务集合污染
  （3 failed），单独运行 3 passed，属既有测试隔离问题，与本次改动无关。
- 真实 PG 并发验收需在具备 PostgreSQL 的环境执行：
  `COMPLAINT_DB_TESTS=1 pytest services/complaint-service/tests/integration/test_session_persistence.py`。

# 批次 4 测试覆盖报告：时间、状态与资源生命周期（T13–T18）

- 日期：2026-11-02
- 测试框架：pytest 7.4.4 + pytest-asyncio（`asyncio_mode=auto`）
- 运行环境：`/tmp/ai_collection_test_venv`，Python 3.11
- 依据：`openspec/changes/audit-findings-remediation/tasks.md` 批次 4、
  `specs/platform/runtime-correctness/spec.md`、`specs/platform/resilience-primitives/spec.md`

## 1. 覆盖汇总

| 任务 | 测试文件 | 用例数 | 正常 | 边界 | 异常 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| T13 | `common/tests/test_t13_time_guard_timezone.py` | 8 | 3 | 4 | 1 | 8 passed |
| T14 | `services/complaint-service/tests/unit/test_t14_session_service_concurrency.py` | 9 | 4 | 4 | 1 | 9 passed |
| T15 | `services/rag-service/tests/test_t15_image_handle_lifecycle.py` | 8 | 2 | 3 | 3 | 8 passed |
| T16 | `common/tests/test_t16_shared_async_client.py` | 6 | 2 | 2 | 2 | 6 passed |
| T17 | `services/mcp-gateway/tests/test_t17_call_log_sink_lifecycle.py` | 10 | 3 | 3 | 4 | 10 passed |
| T18 | `common/tests/test_t18_breaker_open_success.py` | 6 | 2 | 2 | 2 | 6 passed |
| 合计 | 6 个新增文件 | **47** | 16 | 17 | 13 | **47 passed** |

> T15 的「5 个函数 + 1 组 3 参数化用例」合计 8 个用例；T14 含 1 个 `asyncio.gather`
> 并发用例。全部用例均可在无真实网络、无外部 PG/Redis 依赖下运行。

## 2. T13 —— 外呼时段时区感知

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_utc_container_beijing_1000_is_allowed` | 正常 | 容器 `TZ=UTC` 下传入北京时间 10:00 → 判定允许 | ✅ |
| `test_utc_container_beijing_0759_is_denied` | 边界 | 08:00 前 1 分钟 → `BizException(A1001)` | ✅ |
| `test_utc_container_beijing_2059_is_allowed` | 边界 | 21:00 前 1 分钟仍允许（区间左闭右开） | ✅ |
| `test_utc_container_beijing_2100_is_denied` | 边界 | 21:00 整点 → 拒绝（端点开区间） | ✅ |
| `test_naive_clock_is_interpreted_in_business_timezone` | 边界 | naive `datetime` 按 `CALL_TIMEZONE` 解释而非系统 TZ | ✅ |
| `test_business_timezone_drives_decision` | 正常 | 切换 `CALL_TIMEZONE` 后同一时刻判定随之变化 | ✅ |
| `test_live_clock_path_uses_business_timezone` | 正常 | 不传 `now` 时走 `datetime.now(tz)` 实时路径 | ✅ |
| `test_invalid_timezone_name_fails_fast` | 异常 | 非法 IANA 名 → `ZoneInfoNotFoundError`（不静默回退） | ✅ |

## 3. T14 —— SessionService 半锁收敛

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_no_partial_lock_remains` | 正常/结构 | 实例无 `_lock`，模块不再导入 `threading` | ✅ |
| `test_class_documents_single_thread_premise` | 正常/结构 | 类 docstring 明确单线程前提与未来加锁要求 | ✅ |
| `test_idempotent_create_returns_same_session` | 正常 | 同一 complaint_id 重复建会话返回同一 session_id | ✅ |
| `test_create_with_explicit_session_id_is_honored` | 正常 | 显式 session_id 被沿用并登记 | ✅ |
| `test_valid_single_step_and_pipeline_multi_step` | 正常 | 单步与流水线多步流转均合法 | ✅ |
| `test_illegal_transition_and_missing_session_are_rejected` | 异常 | 非法流转返回 False；缺失会话不被隐式创建 | ✅ |
| `test_unknown_status_value_raises` | 异常 | 未知状态值抛错，不静默吞掉 | ✅ |
| `test_timeout_window_is_strict_greater_than` | 边界 | 恰好等于超时窗口不转人工，超过才转 | ✅ |
| `test_concurrent_async_create_yields_one_session` | 边界/并发 | `asyncio.gather` 8 路并发建单仅产生 1 个 session | ✅ |

## 4. T15 —— Pillow 解码器确定性关闭

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_scale_image_returns_scaled_bytes_and_mime` | 正常 | 返回缩放后字节与正确 MIME | ✅ |
| `test_scale_image_keeps_source_format_for_jpeg` | 正常 | JPEG 源保持 JPEG 输出格式 | ✅ |
| `test_image_decoder_is_closed_deterministically` | 边界/资源 | spy `Image.open`，返回前 `img.fp is None` | ✅ |
| `test_fd_count_stays_flat_across_100_images` | 边界 | 连续 100 张图 fd 增量 ≤ 5 | ✅ |
| `test_source_uses_context_manager_only` | 边界/结构 | 源码仅 `with Image.open(...)`，无裸赋值调用 | ✅ |
| `test_invalid_bytes_raise_without_handle_leak`（3 参数化） | 异常 | 空字节 / 非图片 / 截断 PNG → 抛错且不泄漏句柄 | ✅ |

## 5. T16 —— LLM 共享 AsyncClient

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_50_calls_reuse_single_client` | 正常 | 50 次 `post_json` 仅构造 1 个 `AsyncClient` | ✅ |
| `test_explicit_client_bypasses_shared_pool` | 正常 | 显式传入 client 时不进入共享池、不额外构造 | ✅ |
| `test_error_path_does_not_create_extra_client` | 异常 | HTTP 错误路径复用同一 client，不新建 | ✅ |
| `test_closed_shared_client_is_recreated` | 边界 | 共享 client 已关闭 → 下次调用自动重建 | ✅ |
| `test_aclose_is_idempotent` | 边界 | `aclose_shared_client()` 重复调用安全 | ✅ |
| `test_shared_client_is_isolated_per_event_loop` | 边界 | 两个独立事件循环各持有独立 client | ✅ |

## 6. T17 —— call_log sink 句柄生命周期

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_write_then_close_flushes_and_releases_handle` | 正常 | 写入后可读到内容，`close()` 后句柄释放 | ✅ |
| `test_close_is_idempotent` | 正常 | 重复 `close()` 不抛错 | ✅ |
| `test_write_after_close_raises` | 异常 | 关闭后写入 → `RuntimeError("call_log sink is closed")` | ✅ |
| `test_replacing_sink_closes_old_handle` | 正常 | `set_call_log_sink` 替换时关闭旧实例 | ✅ |
| `test_set_none_closes_current_sink` | 边界 | 置空 sink 时关闭当前实例 | ✅ |
| `test_reassigning_same_instance_keeps_handle` | 边界 | 同一实例重复注册不关闭自身 | ✅ |
| `test_sink_without_close_is_tolerated` | 边界 | 无 `close` 的只写 sink 可正常替换 | ✅ |
| `test_close_failure_does_not_block_replacement` | 异常 | 旧 sink `close()` 抛错不阻断替换 | ✅ |
| `test_del_closes_handle` | 异常/兜底 | `gc.collect()` 后 `__del__` 关闭句柄 | ✅ |
| `test_repeated_create_close_does_not_leak_fds` | 边界 | 100 次创建/关闭 fd 水位稳定 | ✅ |

## 7. T18 —— 熔断器 OPEN 态成功语义

| 用例 | 场景类型 | 断言要点 | 结果 |
| --- | --- | --- | --- |
| `test_open_state_success_keeps_failures_and_state` | 正常 | OPEN 态 success → 失败计数/状态/`opened_at` 均不变 | ✅ |
| `test_open_state_success_does_not_extend_reset_window` | 边界 | success 不顺延重置窗口：29s 仍拒绝、30s 放探针 | ✅ |
| `test_open_state_success_emits_no_hook_event` | 异常/副作用 | OPEN 态 success 不触发任何 hook 事件 | ✅ |
| `test_closed_state_success_still_resets_consecutive_failures` | 正常 | CLOSED 态 success 仍清零连续失败 | ✅ |
| `test_half_open_probe_success_closes_and_clears` | 边界 | HALF_OPEN 探针成功 → 闭合且清空 `opened_at` | ✅ |
| `test_open_success_then_failure_stays_open` | 异常 | OPEN 态先 success 后 failure 仍保持 OPEN | ✅ |

## 8. 回归结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 批次 4 聚焦（common 三项） | `pytest common/tests/test_t13_*.py test_t16_*.py test_t18_*.py -q` | **20 passed** |
| T14 聚焦 | `pytest services/complaint-service/tests/unit/test_t14_*.py -q` | **9 passed** |
| T15 聚焦 | `pytest services/rag-service/tests/test_t15_*.py -q` | **8 passed** |
| T17 聚焦 | `pytest services/mcp-gateway/tests/test_t17_*.py -q` | **10 passed** |
| common 全量 | `pytest common/tests -q` | **308 passed, 2 failed**（既有，见 §9） |
| RAG 服务全量 | `pytest services/rag-service/tests -q` | **388 passed, 1 skipped** |
| 工单服务单元全量 | `pytest services/complaint-service/tests/unit -q` | **367 passed, 4 failed, 5 errors**（既有，见 §9） |
| 批次 4 新增合计 | 6 个文件 | **47 passed** |

## 9. 失败项归因（均为既有问题，与 T13–T18 无关）

| 失败项 | 现象 | 归因 |
| --- | --- | --- |
| `common/tests/test_errors*.py`（2 failed） | 既有断言与错误码映射不一致 | 本批次未修改错误码与异常处理器 |
| `test_mcp_real_path.py`（4 failed + 5 errors） | `PydanticUserError: A non-annotated attribute ... query` | 本地 pydantic 2.5 与 MCP SDK 版本不兼容 |
| `test_t07_publisher_task_refs.py` | 进程级全局任务集合被其他用例污染 | 既有测试隔离问题，单独运行通过 |
| mcp-gateway 其他套件 | 集合阶段触发同一 pydantic/MCP SDK 不兼容 | 环境依赖问题；T17 文件单独运行 10 passed |

> 上述失败项在批次 4 改动前即存在，且均不在本次修改文件范围内。

## 10. 静态检查

| 检查 | 范围 | 结果 |
| --- | --- | --- |
| `ruff check` | 本次改动文件（实现 + 测试 + 配置） | 零告警 |
| `python -m compileall` | 本次改动文件 | 通过，无编译错误 |

## 11. 复现命令

```bash
# 按服务隔离运行，避免多服务 conftest 冲突
/tmp/ai_collection_test_venv/bin/python -m pytest \
  common/tests/test_t13_time_guard_timezone.py \
  common/tests/test_t16_shared_async_client.py \
  common/tests/test_t18_breaker_open_success.py -q

/tmp/ai_collection_test_venv/bin/python -m pytest \
  services/complaint-service/tests/unit/test_t14_session_service_concurrency.py -q

/tmp/ai_collection_test_venv/bin/python -m pytest \
  services/rag-service/tests/test_t15_image_handle_lifecycle.py -q

/tmp/ai_collection_test_venv/bin/python -m pytest \
  services/mcp-gateway/tests/test_t17_call_log_sink_lifecycle.py -q
```

## 12. 覆盖缺口与说明

1. **T13 未覆盖跨时区 DST 切换**：当前业务时区为 `Asia/Shanghai`（无 DST）；若切换
   到有 DST 的时区，`ZoneInfo` 已能正确处理，但缺少显式用例。
2. **T14 的并发用例为单事件循环协作式并发**：验证的是「同一 loop 内无 `await` 间隙」，
   不覆盖多线程/多进程场景；该场景以类 docstring 的约束条款而非测试表达。
3. **T15/T17 的 fd 水位**在 macOS 使用 `/dev/fd`，Linux CI 使用 `/proc/self/fd`，
   逻辑一致；CI 上阈值可能因运行时差异略有浮动，已留 5 个余量。
4. **T16 未覆盖真实 TLS/代理链路**：复用断言基于 `httpx.MockTransport`，
   连接池行为（keep-alive、超时）需在集成环境补一次观测。
5. **T17 `__del__` 仅兜底**：用例通过 `gc.collect()` 触发，不承诺生产环境的回收时机；
   确定性释放依赖显式 `close()` 与 `set_call_log_sink` 替换。

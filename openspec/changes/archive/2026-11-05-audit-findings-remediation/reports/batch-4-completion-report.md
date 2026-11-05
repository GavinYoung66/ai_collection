# 批次 4 开发完成报告：时间、状态与资源生命周期（T13–T18）

- 日期：2026-11-02
- 批次：`4. 🟢 时间、状态与资源生命周期`
- 依据：`openspec/changes/audit-findings-remediation/{proposal,design,tasks}.md`、
  `specs/platform/runtime-correctness/spec.md`、`specs/platform/resilience-primitives/spec.md`
- 执行顺序：T13 → T14 → T15 → T16 → T17 → T18（严格按批内编号，未跳项）
- 说明：用户指令中第 2 条沿用了批次 3 的「T09 → T10 → T11 → T12」顺序；本批次实际范围为
  T13–T18，故按 T13→T18 依次执行，以满足第 1 条「第四批次 T13 至 T18」的范围要求。

## 1. 总体完成情况

| 编号 | 问题 | 状态 | 核心文件 | 新增单测 |
| --- | --- | --- | --- | --- |
| T13 | 外呼时段 naive `datetime.now()`，UTC 容器偏 8 小时 | ✅ 完成 | `common/utils/time_guard.py`、`common/config/settings.py` | 8 passed |
| T14 | SessionService 半锁（仅 create_session 加锁） | ✅ 完成 | `services/complaint-service/app/service/session_service.py` | 9 passed |
| T15 | Pillow `Image.open` 未 close | ✅ 完成 | `services/rag-service/app/parsers/asset_extractor.py` | 8 passed |
| T16 | LLM transport 每次新建 `httpx.AsyncClient` | ✅ 完成 | `common/llm/transport.py` | 6 passed |
| T17 | `call_log_sink` 文件句柄永不关闭 | ✅ 完成 | `services/mcp-gateway/app/call_log_sink.py` | 10 passed |
| T18 | 熔断器 OPEN 态 success 语义不严谨 | ✅ 完成 | `common/resilience/breaker.py` | 6 passed |

静态检查：`ruff check` 对本次全部改动文件零告警；`python -m compileall` 全部通过。

## 2. T13 —— 外呼时段时区感知

### 实现要点

1. `common/config/settings.py` 新增平台级配置 `CALL_TIMEZONE: str = "Asia/Shanghai"`；
   `.env.example` 同步示例项。
2. `check_call_time` 不再调用 naive `datetime.now()`：
   - 业务时区由 `ZoneInfo(settings.CALL_TIMEZONE)` 决定，与容器 `TZ` 无关；
   - `datetime.now(tz)` 直接产出业务时区感知时间；
   - 新增可选参数 `now: datetime | None` 支持固定时钟注入（naive 值按业务时区解释，
     aware 值先 `astimezone` 换算）。
3. 非法 `CALL_TIMEZONE` 不做静默回退，调用即抛 `ZoneInfoNotFoundError`（配置错误 fail-fast）。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| `TZ=UTC` 下北京时间 10:00 判定为允许 | ✅ `test_utc_container_beijing_1000_is_allowed` |
| 北京时间 07:59 / 21:00 判定为拒绝 | ✅ 两个边界用例 |
| 时区判定由 `CALL_TIMEZONE` 驱动 | ✅ `test_business_timezone_drives_decision` |
| 固定时钟可注入，不依赖真实时间 | ✅ 全部用例显式传入 `now` |

## 3. T14 —— SessionService 半锁收敛

### 决策

规范允许两条路径：统一加锁，或确认单线程后移除锁并注释。本批次选择**后者**：

- complaint-service 的路由（`app/api/routes/complaints.py`）与三个后台循环
  （`main.py` 队列消费/维护/溢出补偿）全部为 `async def`；
- 全服务无 `asyncio.to_thread` / `run_in_executor` 触达 `SessionService`；
- `create_session` 从入口检查到登记全程无 `await`，事件循环单线程内不可分割。

保留 `threading.Lock` 只能保护 `create_session` 一段，既无法覆盖 `get_session` /
`transition` / `timeout_sessions` 等裸读写，又会在事件循环内造成无谓阻塞，故移除。

### 实现要点

1. 删除 `import threading` 与 `self._lock`，`create_session` / `aload_session`
   去掉锁上下文。
2. 类 docstring 固化单线程前提，并明确「若未来下沉线程池，必须全字段统一加锁」。
3. 保留既有的「查映射 → SETNX → 双重检查 → 登记」幂等链路，仅去掉锁层。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 不再存在半锁 | ✅ `test_no_partial_lock_remains`（实例无 `_lock`、模块无 threading） |
| 单线程前提有注释 | ✅ `test_class_documents_single_thread_premise` |
| 并发建单仅一个 session | ✅ `test_concurrent_async_create_yields_one_session` |
| 状态机与超时边界不回归 | ✅ 单步/多步流转、非法流转、严格大于超时窗口 |

## 4. T15 —— Pillow 解码器确定性关闭

### 实现要点

`_scale_image` 改为上下文管理器，`format` 读取与 `save` 均发生在句柄存活期内：

```python
with Image.open(io.BytesIO(binary)) as img:
    img.thumbnail((settings.KB_ASSETS_MAX_SIZE, settings.KB_ASSETS_MAX_SIZE))
    fmt = (img.format or "PNG").upper()
    ...
    img.save(buf, format=save_fmt)
    return buf.getvalue(), mime
```

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 批量 100 张图 fd 不线性增长 | ✅ `test_fd_count_stays_flat_across_100_images`（增量 ≤ 5） |
| 解码器实例在返回前已关闭 | ✅ `test_image_decoder_is_closed_deterministically`（`fp is None`） |
| 裸 `Image.open` 调用零命中 | ✅ `test_source_uses_context_manager_only` |
| 缩放结果与 MIME 保持 | ✅ PNG/JPEG 正常用例 |
| 坏字节异常不吞 | ✅ 3 组参数化异常用例 |

## 5. T16 —— LLM 共享 AsyncClient

### 实现要点

1. 新增模块级 `_shared_clients: WeakKeyDictionary[AbstractEventLoop, httpx.AsyncClient]`：
   - 同一事件循环内复用同一 client，保留 keep-alive 连接池；
   - 以事件循环对象为弱引用 key，测试/热重载切换 loop 时新建，
     避免跨 loop 复用已绑定连接池；
   - client 已关闭时自动重建。
2. `post_json` 未显式传 `client` 时改用 `_get_shared_client()`，不再
   `async with httpx.AsyncClient()` 每次新建。
3. 新增 `aclose_shared_client()` 显式关闭入口（幂等），满足长生命周期单例的关闭契约。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 50 次调用仅创建 1 个 client | ✅ `test_50_calls_reuse_single_client` |
| 显式传入 client 不入共享池 | ✅ `test_explicit_client_bypasses_shared_pool` |
| HTTP 错误路径不额外建 client | ✅ `test_error_path_does_not_create_extra_client` |
| 关闭后按需重建 | ✅ `test_closed_shared_client_is_recreated` |
| 不同事件循环隔离 | ✅ `test_shared_client_is_isolated_per_event_loop` |

## 6. T17 —— call_log sink 句柄生命周期

### 实现要点

1. `TempFileCallLogSink`：
   - 新增幂等 `close()`：先置空 `_fh` 再 flush + close，重复调用安全；
   - 新增 `__del__` 兜底（异常全部 suppress，析构阶段不抛）；
   - `write` 在关闭后抛 `RuntimeError("call_log sink is closed")`，
     避免向已释放句柄静默写入。
2. `set_call_log_sink`：
   - 先切换 `_sink`，再对旧 sink 调用 `close()`（若实现）；
   - 同一实例重复注册不关闭自身；
   - 旧 sink 无 `close` 或 `close` 抛错均不阻断替换。
3. `NullCallLogSink` 补 `close()`，与协议一致。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| 替换 sink 后旧句柄关闭 | ✅ `test_replacing_sink_closes_old_handle` |
| 退出前 flush 完成 | ✅ 写入后立即 `read_text` 可见 + close 幂等 |
| 清空 sink 关闭当前实例 | ✅ `test_set_none_closes_current_sink` |
| `__del__` 兜底关闭 | ✅ `test_del_closes_handle` |
| 100 次创建/关闭 fd 稳定 | ✅ `test_repeated_create_close_does_not_leak_fds` |

## 7. T18 —— 熔断器 OPEN 态成功语义

### 实现要点

```python
async def record_success(self) -> None:
    if self._state is BreakerState.OPEN:
        return
    self._failures = 0
    if self._state is BreakerState.HALF_OPEN:
        self._state = BreakerState.CLOSED
        self._opened_at = None
```

OPEN 态的成功上报（被拒绝请求的迟到回执）不再清零失败计数、不改变状态、
不重置 `opened_at`；状态迁移仅由失败阈值、重置窗口与探针结果驱动。

### 验收对照

| 验收项 | 结果 |
| --- | --- |
| OPEN 态 success 后计数/状态不变 | ✅ `test_open_state_success_keeps_failures_and_state` |
| 重置窗口不被 success 顺延 | ✅ `test_open_state_success_does_not_extend_reset_window` |
| success 不触发 open hook 事件 | ✅ `test_open_state_success_emits_no_hook_event` |
| CLOSED 态 success 仍清零连续失败 | ✅ `test_closed_state_success_still_resets_consecutive_failures` |
| HALF_OPEN 探针成功闭合 | ✅ `test_half_open_probe_success_closes_and_clears` |

## 8. 变更文件清单

| 类型 | 文件 |
| --- | --- |
| 实现 | `common/utils/time_guard.py` |
| 实现 | `common/config/settings.py` |
| 实现 | `common/llm/transport.py` |
| 实现 | `common/resilience/breaker.py` |
| 实现 | `services/complaint-service/app/service/session_service.py` |
| 实现 | `services/rag-service/app/parsers/asset_extractor.py` |
| 实现 | `services/mcp-gateway/app/call_log_sink.py` |
| 配置示例 | `.env.example`（新增 `CALL_TIMEZONE`） |
| 测试 | `common/tests/test_t13_time_guard_timezone.py`（新增） |
| 测试 | `common/tests/test_t16_shared_async_client.py`（新增） |
| 测试 | `common/tests/test_t18_breaker_open_success.py`（新增） |
| 测试 | `services/complaint-service/tests/unit/test_t14_session_service_concurrency.py`（新增） |
| 测试 | `services/rag-service/tests/test_t15_image_handle_lifecycle.py`（新增） |
| 测试 | `services/mcp-gateway/tests/test_t17_call_log_sink_lifecycle.py`（新增） |
| 规范 | `openspec/changes/audit-findings-remediation/tasks.md`（T13–T18 勾选与状态回写） |

## 9. 口径与遗留

1. **T14 的「统一加锁」未采用**：本批次以「确认单线程后移除锁 + 注释」满足规范二选一。
   若后续把 `SessionService` 迁入线程池或同步端点，必须按类 docstring 改为全字段加锁。
2. **T16 共享 client 的关闭入口未接入各服务 lifespan**：`aclose_shared_client()` 已提供并
   幂等；进程退出时由 httpx 资源回收兜底。是否在各服务 shutdown 显式调用属部署接线，
   不影响本任务验收（复用计数）。
3. **T13 非法时区在调用点抛出**，未做启动期校验；如需生产 fail-fast，可后续在
   `validate_required_secrets` 同类启动校验处增加配置合法性检查。
4. **T17 的 `__del__` 仅作兜底**：确定性释放依赖显式 `close()` / 替换时关闭，
   不承诺 GC 时机。

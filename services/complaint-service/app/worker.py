"""complaint-service 独立后台 worker（批次6 / T25）。

FastAPI 进程（``app.main``）只处理 HTTP 请求；原先进程内的三组常驻循环
——排队消费、周期维护、溢出补偿——以及 EventBus 订阅，统一在本模块的
独立进程内运行。

启动方式：
    python -m app.worker

设计边界（design.md Non-Goals）：只做进程剥离，不重构业务逻辑；
循环函数保持与原 ``app.main`` 内实现逐行等价。
"""
from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from app.config import settings
from common.config.logging import setup_logging

setup_logging(env=getattr(settings, "ENV", "dev"), level=getattr(settings, "LOG_LEVEL", None))

logger = structlog.get_logger()

# 队列消费循环轮询间隔（秒）
_QUEUE_CONSUME_INTERVAL = 1.0

# 溢出补偿循环（阶段3，design D4）：BRPOP 空转/异常后的休眠间隔（防空转刷 Redis）；
# full 回塞前的指数退避起点与上限（上限 2s）。
_DEFERRED_IDLE_INTERVAL = 1.0
_DEFERRED_BACKOFF_BASE = 0.05
_DEFERRED_BACKOFF_MAX = 2.0

# 周期维护循环间隔（秒）：超时转人工 + checkpoint 三表裁剪。
# 5min 足够频繁（崩溃后最长 5min 内把滞留工单转人工），又不至于给 PG 加压。
_MAINTENANCE_INTERVAL = 300.0

# 在途排队研判任务强引用集合：事件循环仅弱持有 asyncio.Task，
# 不保存返回值时任务可能在执行中被 GC（中途消失 → 槽/幂等锁不释放）。
# 任务完成后经 done_callback 自动移出集合。
_running_queued: set[asyncio.Task] = set()


def spawn_queued_complaint(complaint_id: str) -> None:
    """派发排队工单研判并持有任务引用至其结束。"""
    task = asyncio.create_task(_run_queued_complaint(complaint_id))
    _running_queued.add(task)
    task.add_done_callback(_running_queued.discard)


async def _run_queued_complaint(complaint_id: str) -> None:
    """消费循环已为该工单预留并发槽：执行研判并释放槽 + 维5幂等锁。"""
    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import rate_limiter

    try:
        await complaint_service.judge_async(complaint_id, run_graph=True)
    except asyncio.CancelledError:
        # shutdown 取消：best-effort 把滞留 judging 的 session 推进 pending_review。
        logger.warning("queue_consumer_judge_cancelled", complaint_id=complaint_id)
        try:
            marked = await complaint_service.fail_shutdown(complaint_id)
            if not marked:
                logger.info(
                    "queue_consumer_shutdown_mark_skipped",
                    complaint_id=complaint_id,
                    reason="session_not_in_pipeline",
                )
        except Exception as e:  # noqa: BLE001 — 关停兜底失败不阻塞槽/锁清理
            logger.warning(
                "queue_consumer_shutdown_mark_failed",
                complaint_id=complaint_id,
                error=str(e),
            )
        raise
    except Exception as e:  # noqa: BLE001 — 单工单失败不拖垮消费循环
        logger.warning("queue_consumer_judge_failed", complaint_id=complaint_id, error=str(e))
    finally:
        rate_limiter.release_complaint()
        await rate_limiter.release_complaint_lock(complaint_id)


async def consume_queue_once() -> None:
    """消费一轮排队工单（G8 接线，供消费循环与测试复用）。

    - ready：已预留并发槽，直接派发执行；
    - expired：排队超 10min → A5010 转人审，并释放幂等锁。
    """
    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import rate_limiter

    ready, expired = await rate_limiter.consume_pending()
    for cid in expired:
        logger.warning("queue_consumer_expired", complaint_id=cid)
        try:
            await complaint_service.fail_queue_timeout(cid)
        except Exception as e:  # noqa: BLE001
            logger.warning("queue_consumer_expire_failed", complaint_id=cid, error=str(e))
    for cid in ready:
        spawn_queued_complaint(cid)


async def queue_consumer_loop() -> None:
    while True:
        try:
            await consume_queue_once()
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning("queue_consumer_loop_error", error=str(e))
        await asyncio.sleep(_QUEUE_CONSUME_INTERVAL)


async def maintenance_loop() -> None:
    """周期维护循环：超时转人工 + checkpoint 三表裁剪。

    多副本下每个实例都会跑此循环，但 `run_maintenance` 内部有跨副本锁
    （maintenance_lock），保证同刻只有一个实例真正执行，其余跳过。
    """
    from app.service.maintenance import run_maintenance

    while True:
        try:
            await run_maintenance()
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning("maintenance_loop_error", error=str(e))
        await asyncio.sleep(_MAINTENANCE_INTERVAL)


async def deferred_consumer_loop() -> None:
    """HTTP 队列满溢出 Redis 延迟队列补偿循环（阶段3，design D4）。

    仅开关开启时启动。BRPOP 取单 → acquire_complaint 重新接入：
    - granted   → judge_async 执行（复用在途守卫/图锁/排队 deadline 幂等），
                  结束/异常/取消均释放并发槽 + 维5锁；
    - queued    → 已进内存队列，由既有 queue_consumer_loop 接管；
    - duplicate → 在途/已完成，幂等忽略（不重塞）；
    - full      → 指数退避（上限 2s）后重塞队尾，禁止无退避空转刷 Redis。

    关停不丢（3.5）：取出后未成功接入且未重塞即被取消/异常中断 → 塞回队列，
    保证重启后队列仍有该 cid（幂等由维5锁 + 图锁 + 在途集合保证只研判一次）。
    """
    from app.service.complaint_service import complaint_service
    from app.service.rate_limiter import (
        OUTCOME_DUPLICATE,
        OUTCOME_FULL,
        OUTCOME_GRANTED,
        OUTCOME_QUEUED,
        rate_limiter,
    )

    backoff = _DEFERRED_BACKOFF_BASE
    while True:
        cid: str | None = None
        settled = False  # 已明确处置：接入完成 / 已重塞 / 幂等忽略
        try:
            cid = await rate_limiter.deferred_pop()
            if cid is None:
                # 队列空（BRPOP 超时）或 Redis 故障：休眠防无退避空转刷 Redis
                settled = True
                backoff = _DEFERRED_BACKOFF_BASE
                await asyncio.sleep(_DEFERRED_IDLE_INTERVAL)
                continue
            outcome = await rate_limiter.acquire_complaint(cid)
            if outcome == OUTCOME_GRANTED:
                try:
                    await complaint_service.judge_async(cid, run_graph=True)
                except asyncio.CancelledError:
                    raise  # 关停中断 → 处置未完成，finally 重塞
                except Exception as e:  # noqa: BLE001 — 单工单失败不拖垮循环
                    logger.warning(
                        "deferred_consumer_judge_failed",
                        complaint_id=cid,
                        error=str(e),
                    )
                finally:
                    await rate_limiter.release_complaint_lock(cid)
                    rate_limiter.release_complaint()
                settled = True
            elif outcome == OUTCOME_QUEUED:
                settled = True  # 已进内存队列，由既有消费循环接管
            elif outcome == OUTCOME_FULL:
                # 仍满：指数退避后重塞队尾（LPUSH + BRPOP 保持 FIFO）
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _DEFERRED_BACKOFF_MAX)
                await rate_limiter.deferred_requeue(cid)
                settled = True
            elif outcome == OUTCOME_DUPLICATE:
                settled = True  # 在途/已完成，幂等忽略（不重塞）
        except asyncio.CancelledError:
            # 关停：已取出但未处置的 cid 必须重塞，保证重启后队列仍有该 cid。
            if cid is not None and not settled:
                with contextlib.suppress(Exception):
                    await asyncio.shield(rate_limiter.deferred_requeue(cid))
            raise
        except Exception as e:  # noqa: BLE001 — 循环永不退出
            logger.warning("deferred_consumer_loop_error", complaint_id=cid, error=str(e))
            if cid is not None and not settled:
                with contextlib.suppress(Exception):
                    await rate_limiter.deferred_requeue(cid)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _DEFERRED_BACKOFF_MAX)


async def start_background_tasks() -> tuple[list[asyncio.Task], asyncio.Task | None]:
    """创建 worker 的常驻任务（排队消费 / 周期维护 / 溢出补偿 / EventBus 订阅）。"""
    tasks = [
        asyncio.create_task(queue_consumer_loop(), name="cs-queue-consumer"),
        asyncio.create_task(maintenance_loop(), name="cs-maintenance"),
    ]
    if settings.COMPLAINT_DEFERRED_QUEUE_ENABLED:
        tasks.append(asyncio.create_task(deferred_consumer_loop(), name="cs-deferred-consumer"))

    from app.events.subscriber import start_eventbus_subscriber

    eventbus_task = await start_eventbus_subscriber()
    return tasks, eventbus_task


async def _startup() -> None:
    """worker 启动装配：密钥 fail-fast、链路追踪、hooks、checkpointer。"""
    from common.config.settings import validate_required_secrets

    validate_required_secrets(settings)

    from common.trace.langfuse_gate import configure_langfuse_env
    from common.trace.langsmith_gate import configure_langsmith_env

    configure_langsmith_env(settings)
    configure_langfuse_env(settings)

    from common.security.rbac import log_auth_mode

    log_auth_mode(settings, "complaint-worker")

    from app.bootstrap import wire_resilience_hooks

    wire_resilience_hooks()

    if not getattr(settings, "USE_MOCK_MCP", True):
        from app.mcp.validate import validate_real_mcp_config

        validate_real_mcp_config()

    if not getattr(settings, "USE_MOCK_LLM", True):
        from app.llm.validate import validate_real_llm_config

        validate_real_llm_config()

    from app.graph.checkpointer import init_checkpointer

    await init_checkpointer()


async def _shutdown(tasks: list[asyncio.Task], eventbus_task: asyncio.Task | None) -> None:
    """与 main lifespan 同序关停：先停循环，再停在途研判，最后关资源。"""
    if eventbus_task is not None:
        eventbus_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await eventbus_task
        logger.info(
            "complaint_eventbus_subscriber_shutdown",
            cancelled=eventbus_task.cancelled() or eventbus_task.done(),
        )

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task

    for task in list(_running_queued):
        task.cancel()
    for task in list(_running_queued):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    from app.bootstrap import unwire_resilience_hooks

    unwire_resilience_hooks()

    # T57：关闭共享 LLM HTTP client（worker 进程跑 invoke_llm 研判，按 loop
    # 隔离的 client 在关停时回收；失败不阻断关停）
    try:
        from common.llm.transport import aclose_shared_client

        await aclose_shared_client()
    except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
        logger.warning("worker_llm_shared_client_aclose_failed", exc_info=True)

    # W4：关闭 resilience 共享 Redis 连接池（rate_limit/idempotency 共用）。
    try:
        from common.resilience._redis import aclose_redis

        await aclose_redis()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("worker_resilience_redis_aclose_failed", exc_info=True)

    # T58：释放 SQLAlchemy engine 连接池（PG 连接归零）。
    try:
        from common.db.session import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("worker_engine_dispose_failed", exc_info=True)

    try:
        from app.api.deps import get_mcp_client

        await get_mcp_client().aclose()
    except Exception:  # noqa: BLE001 — 关闭失败不阻断 shutdown
        logger.warning("worker_mcp_client_aclose_failed", exc_info=True)

    try:
        from app.graph.checkpointer import shutdown_checkpointer

        await shutdown_checkpointer()
    except Exception:  # noqa: BLE001 — 关池失败不阻断 shutdown
        logger.warning("worker_checkpointer_shutdown_failed", exc_info=True)


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(sig, stop.set)


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """启动 worker 并阻塞到收到停止信号；供进程入口与测试复用。"""
    await _startup()
    tasks, eventbus_task = await start_background_tasks()
    stop = stop_event or asyncio.Event()
    if stop_event is None:
        _install_stop_handlers(stop)
    try:
        await stop.wait()
    finally:
        await _shutdown(tasks, eventbus_task)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()

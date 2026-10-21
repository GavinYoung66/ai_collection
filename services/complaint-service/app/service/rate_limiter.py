"""限流服务（9.5）—— 用 P0 零件拼装五维（spec: complaint-service/rate-limit）。

维1 并发闸   common.resilience.Semaphore(max_active=20)，满即排队、不阻塞等待
维2 削峰队列 common.resilience.BoundedQueue(queue_max=500)，满 → "full"（路由层产 B0006/429）
维3 MCP 令牌 rate_limit.allow(key=f"csmp:mcp:{server}", limit=qps, window=1)，按 server 分桶
维4 动态消费 队列深度 >250 消费 x2、>400 x3
维5 单工单锁 idempotency.acquire(key=f"csmp:complaint:judge_lock:{cid}", ttl=1800)

不变项：阈值 max_active=20/queue_max=500（config/rate_limiter.yaml）；
用户维 key 解析 X-User-Id 优先回退 IP；Redis 挂时限流维度静默放行（warn 日志）。

G5 两级 deadline key 亦在本模块维护（互相独立，不删对方的 key）：
- queue_start：入队 SET EX=queue_ttl(10min)；judge_async 入口 key 已失效 → A5010 转人审
- exec_start：graph 执行前 SET EX=exec_ttl(30min)；执行结束（含超时）即删
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping, Optional

import structlog
from redis.exceptions import RedisError

from app.config import settings
from app.utils.redis_keys import (
    TTL_COMPLAINT_LOCK,
    TTL_EXEC_START,
    deferred_queue_key,
    exec_start_key,
    judge_lock_key,
    queue_start_key,
)
from common.resilience._redis import configure_redis, get_redis
from common.resilience.concurrency import Semaphore
from common.resilience.idempotency import acquire as idempotency_acquire
from common.resilience.queue import BoundedQueue
from common.resilience.rate_limit import allow as rate_limit_allow

logger = structlog.get_logger()

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "rate_limiter.yaml"

# 接入结果（路由层据此映射：granted/queued → 200，duplicate → 幂等拒绝，full → B0006/429）
OUTCOME_GRANTED = "granted"
OUTCOME_QUEUED = "queued"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_FULL = "full"

# 维4 动态消费阈值（队列绝对深度，design §4）
_DYNAMIC_X2_DEPTH = 250
_DYNAMIC_X3_DEPTH = 400

# 溢出接入原子脚本（design D4）：LLEN 容量判定与 LPUSH 单次 EVAL 内完成，
# 消除「检查-入队」窗口，高并发下不软超限。KEYS[1]=延迟队列 key；
# ARGV[1]=兜底容量；ARGV[2]=complaint_id。返回新深度；-1 = 容量满。
_DEFERRED_OFFER_LUA = """
local depth = redis.call('LLEN', KEYS[1])
if depth >= tonumber(ARGV[1]) then
  return -1
end
redis.call('LPUSH', KEYS[1], ARGV[2])
return depth + 1
"""


def _event_context(trace_id: str = "", *, node: str = "") -> dict | None:
    """构造韧性事件业务上下文：空值字段不下发（零件层事件至少携带 key/reason）。"""
    ctx: dict[str, Any] = {}
    if trace_id:
        ctx["trace_id"] = trace_id
    if node:
        ctx["node"] = node
    return ctx or None


def _load_config() -> dict:
    defaults = {
        "complaint": {
            "max_active": 20,
            "queue_max": 500,
            "queue_timeout_minutes": 10,
            "exec_timeout_seconds": TTL_EXEC_START,
            "lock_ttl_seconds": TTL_COMPLAINT_LOCK,
        },
        "mcp": {"qps": {"business_ticket": 50, "call_qc": 30, "rag_kb": 20, "data_platform": 30}},
        "user": {"qps": 200, "window": 1},
        "queue": {"dynamic_consume": True, "base_consume_rate": 5},
    }
    try:
        import yaml

        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v
            elif isinstance(v, dict):
                for sk, sv in v.items():
                    cfg[k].setdefault(sk, sv)
        return cfg
    except Exception:  # noqa: BLE001 — 配置缺失/损坏走内置默认，不阻断启动
        return defaults


class RateLimiter:
    """五维限流器（全部 I/O 维度为 async；内存闸无 I/O）。

    Args:
        config: 配置字典，None 时读 config/rate_limiter.yaml（缺省走内置默认）。
        redis_client: 注入 Redis 客户端（测试用 fake）；同时注入 common 共享客户端，
            保证 rate_limit / idempotency 与本模块 deadline 读写同一实例。
    """

    def __init__(self, config: Optional[dict] = None, redis_client: Any = None) -> None:
        self._cfg = config or _load_config()
        if redis_client is not None:
            configure_redis(redis_client)
        comp = self._cfg["complaint"]
        self._max_active = int(comp["max_active"])
        self._queue_max = int(comp["queue_max"])
        self._queue_ttl = int(comp.get("queue_timeout_minutes", 10)) * 60
        self._exec_timeout = int(comp.get("exec_timeout_seconds", TTL_EXEC_START))
        self._lock_ttl = int(comp.get("lock_ttl_seconds", TTL_COMPLAINT_LOCK))
        qcfg = self._cfg.get("queue", {})
        self._dynamic = bool(qcfg.get("dynamic_consume", True))
        self._base_rate = int(qcfg.get("base_consume_rate", 5))
        ucfg = self._cfg.get("user", {})
        self._user_qps = int(ucfg.get("qps", 200))
        self._user_window = int(ucfg.get("window", 1))
        self._mcp_qps: dict[str, int] = dict(self._cfg["mcp"]["qps"])
        # 溢出延迟队列（design D4）：容量默认走 settings（env 驱动），yaml deferred.max 可选覆盖
        dcfg = self._cfg.get("deferred") or {}
        self._deferred_max = int(dcfg.get("max", settings.COMPLAINT_DEFERRED_QUEUE_MAX))

        # 维1/维2：进程内有界零件
        self._sem = Semaphore(self._max_active)
        self._queue = BoundedQueue(self._queue_max)
        # 在队工单成员表：区分「直接获槽」与「排队中」，供 judge_async 入口 deadline 检查
        self._queued: set[str] = set()

    @property
    def config(self) -> dict:
        return self._cfg

    @property
    def max_active(self) -> int:
        return self._max_active

    @property
    def inflight(self) -> int:
        return self._sem.inflight

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def exec_timeout_seconds(self) -> int:
        return self._exec_timeout

    # ── 维1 + 维2 + 维5：工单接入 ──────────────────────────

    async def acquire_complaint(self, complaint_id: str) -> str:
        """返回 granted / queued / duplicate / full。

        维5 先抢单工单锁；维1 并发闸有槽直接执行；维1 满走维2 有界队列，
        入队同时写 queue_start（10min TTL）；队列满返回 full（→ B0006）。
        """
        acquired = await idempotency_acquire(
            judge_lock_key(complaint_id), ttl=self._lock_ttl
        )
        if not acquired:
            # P0 幂等零件 Redis 挂保守拒绝；本服务限流契约要求 Redis 挂放行：
            # 经 ping 区分「真重复」与「Redis 故障」，故障时降级放行（内存闸仍生效）。
            if await self._redis_unavailable():
                logger.warning(
                    "rate_limiter_lock_degraded_allow", complaint_id=complaint_id
                )
            else:
                return OUTCOME_DUPLICATE

        if await self._sem.acquire():
            return OUTCOME_GRANTED

        if await self._queue.push(complaint_id):
            self._queued.add(complaint_id)
            await self._mark_queue_start(complaint_id)
            logger.info(
                "rate_limiter_queued",
                complaint_id=complaint_id,
                depth=self._queue.qsize(),
                inflight=self._sem.inflight,
            )
            return OUTCOME_QUEUED
        # 队列满（→B0006）：本次未接入，须回滚维5幂等锁，否则重试会被误判重复
        await self.release_complaint_lock(complaint_id)
        return OUTCOME_FULL

    def release_complaint(self) -> None:
        """工单执行结束释放并发槽。"""
        self._sem.release()

    async def release_complaint_lock(self, complaint_id: str) -> None:
        """执行结束释放维5单工单幂等锁。

        并发期由锁防重复执行；执行结束后释放，允许后续合法重判
        （否则 SETNX 锁会阻塞同一工单 30min 内的再次研判）。
        """
        try:
            await get_redis().delete(judge_lock_key(complaint_id))
        except (RedisError, OSError) as e:
            logger.warning(
                "rate_limiter_lock_release_failed", complaint_id=complaint_id, error=str(e)
            )

    # ── 维4：动态消费 ────────────────────────────────────

    async def consume_pending(
        self, max_n: int | None = None
    ) -> tuple[list[str], list[str]]:
        """按动态速率从队首取工单。

        返回 (ready, expired)：
        - ready：queue_start 仍在 10min TTL 内且已为其预留并发槽；
        - expired：排队超 10min（queue_start 已失效），调用方转 pending_review + A5010。
        """
        if not self._dynamic:
            return [], []
        n = max_n if max_n is not None else self._base_rate
        depth = self._queue.qsize()
        if depth > _DYNAMIC_X3_DEPTH:
            n = min(n * 3, self._base_rate * 3)
        elif depth > _DYNAMIC_X2_DEPTH:
            n = min(n * 2, self._base_rate * 2)

        ready: list[str] = []
        expired: list[str] = []
        for _ in range(n):
            item = self._queue.pop_nowait()
            if item is None:
                break
            if not await self._queue_start_alive(item):
                # 排队超 10min：弹出不执行（5.1）
                self._queued.discard(item)
                expired.append(item)
                continue
            if await self._sem.acquire():
                await self._delete_queue_start(item)
                self._queued.discard(item)
                ready.append(item)
            else:
                # 仍无空槽：退回队尾，保留成员与 queue_start（下次再判 deadline）
                requeued = await self._queue.push(item)
                if not requeued:
                    # pop 腾出的空位在 await 间隙被新到达请求重新填满，
                    # 工单已出队且无法回队：绝不能静默丢失（否则永久挂"排队中"），
                    # 按 expired 弹出 → 调用方落 A5010 转人审并释放幂等锁。
                    self._queued.discard(item)
                    await self._delete_queue_start(item)
                    logger.warning(
                        "rate_limiter_requeue_full", complaint_id=item
                    )
                    expired.append(item)
        return ready, expired

    # ── 维3：MCP server 分桶令牌（window=1s）──────────────
    # 生产链路的 MCP per-server QPS 统一由 mcp-gateway 的 MCPRateLimitMiddleware
    # 治理（key=csmp:mcp_ratelimit:{server}，超限 429 拒绝且不触达下游 server）。
    # 本方法为备用/单元测试用途，未接入 real_transport.call 调用链，
    # 避免与网关双重计数导致误限流。

    async def acquire_mcp(self, server: str, trace_id: str = "") -> bool:
        qps = self._mcp_qps.get(server)
        if qps is None:
            return True
        return await rate_limit_allow(
            f"csmp:mcp:{server}",
            limit=qps,
            window=1,
            context=_event_context(trace_id, node=f"mcp:{server}"),
        )

    # ── 用户维：X-User-Id 优先回退 IP ─────────────────────

    @staticmethod
    def resolve_user_key(
        headers: Mapping[str, str] | None, client_host: str | None = None
    ) -> str:
        user = ""
        if headers:
            user = headers.get("X-User-Id") or headers.get("x-user-id") or ""
        return user or client_host or "anonymous"

    async def acquire_user(
        self,
        user_key: str,
        bucket: str = "admission",
        limit: int | None = None,
        window: int | None = None,
        trace_id: str = "",
    ) -> bool:
        """用户维固定窗口；Redis 挂由 rate_limit 零件静默放行。"""
        return await rate_limit_allow(
            f"csmp:ratelimit:{bucket}:{user_key}",
            limit=limit or self._user_qps,
            window=window or self._user_window,
            context=_event_context(trace_id, node=f"rate_limit:{bucket}"),
        )

    # ── G5：两级 deadline ────────────────────────────────

    async def check_queue_deadline(self, complaint_id: str) -> bool:
        """judge_async 入口检查（5.1）。

        - 未排过队（直接获槽）：放行，不动 Redis；
        - queue_start 仍在 TTL 内：删 queue_start（获槽执行）放行；
        - queue_start 已失效（排队 >10min）：弹出，返回 False（→ A5010 转人审）。
        Redis 不可达时 fail-open（无法判定年龄，不误杀人审）。
        """
        if complaint_id not in self._queued:
            return True
        if await self._queue_start_alive(complaint_id):
            await self._delete_queue_start(complaint_id)
            self._queued.discard(complaint_id)
            return True
        self._queued.discard(complaint_id)
        logger.warning("rate_limiter_queue_expired", complaint_id=complaint_id)
        return False

    async def start_execution(self, complaint_id: str) -> None:
        """graph 执行前写 exec_start（30min TTL，5.2）。失败仅 warn，不阻断研判。"""
        try:
            await get_redis().set(
                exec_start_key(complaint_id), str(time.time()), ex=self._exec_timeout
            )
        except (RedisError, OSError) as e:
            logger.warning("rate_limiter_exec_start_failed", error=str(e))

    async def finish_execution(self, complaint_id: str) -> None:
        """graph 执行结束（含超时）即删 exec_start（5.3：与 queue_start 互不干扰）。"""
        try:
            await get_redis().delete(exec_start_key(complaint_id))
        except (RedisError, OSError) as e:
            logger.warning("rate_limiter_exec_finish_failed", error=str(e))

    # ── 溢出延迟队列（surge hardening 阶段3，design D4）──────────────

    async def deferred_offer(self, complaint_id: str) -> bool:
        """满溢出接入：原子「容量判定 + LPUSH」入 Redis 延迟队列（FIFO）并返回 True。

        返回 False（路由层回退 B0006，fail-closed）：兜底容量已满（脚本返回 -1），
        或 Redis 故障。容量检查与入队在单次 EVAL 内完成，高并发下不软超限。
        """
        try:
            depth = await get_redis().eval(
                _DEFERRED_OFFER_LUA,
                1,
                deferred_queue_key(),
                self._deferred_max,
                complaint_id,
            )
            if depth < 0:
                logger.warning(
                    "deferred_queue_full",
                    complaint_id=complaint_id,
                    depth=self._deferred_max,
                )
                return False
            logger.info("deferred_queue_offered", complaint_id=complaint_id, depth=depth)
            return True
        except (RedisError, OSError) as e:
            logger.warning(
                "deferred_offer_failed", complaint_id=complaint_id, error=str(e)
            )
            return False

    async def deferred_pop(self, timeout: int = 5) -> Optional[str]:
        """BRPOP 取单（LPUSH 左进、BRPOP 右出 = FIFO）。

        None 表示超时或 Redis 故障（故障日志已 warn，由调用方退避防空转）。
        """
        try:
            item = await get_redis().brpop(deferred_queue_key(), timeout=timeout)
            if not item:
                return None
            return item[1]
        except (RedisError, OSError) as e:
            logger.warning("deferred_pop_failed", error=str(e))
            return None

    async def deferred_requeue(self, complaint_id: str) -> None:
        """重塞回延迟队列（指数退避 sleep 由调用方控制，方法内不阻塞）。

        Redis 故障仅 warn——Redis 挂时整个延迟队列本身不可用，无可回塞目的地，
        由维5锁 TTL/调用方重试兜底，不阻断补偿循环。
        """
        try:
            await get_redis().lpush(deferred_queue_key(), complaint_id)
        except (RedisError, OSError) as e:
            logger.warning(
                "deferred_requeue_failed", complaint_id=complaint_id, error=str(e)
            )

    # ── 内部：Redis 操作（统一故障口径） ───────────────────

    async def _redis_unavailable(self) -> bool:
        try:
            return not bool(await get_redis().ping())
        except (RedisError, OSError):
            return True

    async def _mark_queue_start(self, complaint_id: str) -> None:
        try:
            await get_redis().set(
                queue_start_key(complaint_id), str(time.time()), ex=self._queue_ttl
            )
        except (RedisError, OSError) as e:
            # Redis 挂放行：入队不阻断；deadline 检查 fail-open
            logger.warning("rate_limiter_queue_start_failed", error=str(e))

    async def _queue_start_alive(self, complaint_id: str) -> bool:
        try:
            return await get_redis().get(queue_start_key(complaint_id)) is not None
        except (RedisError, OSError):
            return True

    async def _delete_queue_start(self, complaint_id: str) -> None:
        try:
            await get_redis().delete(queue_start_key(complaint_id))
        except (RedisError, OSError) as e:
            logger.warning("rate_limiter_queue_start_delete_failed", error=str(e))


rate_limiter = RateLimiter()

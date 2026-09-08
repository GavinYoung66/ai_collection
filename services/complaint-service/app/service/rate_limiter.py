"""限流服务（9.5）。

5 维限流（配置来自 config/rate_limiter.yaml）：
1. 工单接入 Semaphore(max_active=20) + csmp:queue:pending (max 500, 超时 10min)
2. MCP 令牌桶按 Server
3. LLM 令牌桶按模型
4. 高投诉涌入动态消费
5. 单工单 SETNX 锁 csmp:complaint:lock:{id} TTL=30min
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from app.utils.redis_keys import TTL_COMPLAINT_LOCK, complaint_lock_key, queue_pending_key

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "rate_limiter.yaml"
QUEUE_KEY = queue_pending_key()


def _load_config() -> dict:
    defaults = {
        "complaint": {"max_active": 20, "queue_max": 500,
                      "queue_timeout_minutes": 10,
                      "lock_ttl_seconds": TTL_COMPLAINT_LOCK},
        "mcp": {"qps": {"business_ticket": 50, "call_quality": 30, "rag": 20}},
        "llm": {"qps": {"deepseek_r1": 5, "qwen_plus": 10, "qwen_turbo": 30}},
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
    except Exception:
        return defaults


class _TokenBucket:
    def __init__(self, rate: float) -> None:
        self._rate = rate
        self._tokens = rate
        self._last = time.time()

    def acquire(self) -> bool:
        now = time.time()
        elapsed = now - self._last
        self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
        self._last = now
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class _InMemoryRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._zset: dict[str, list[tuple[float, str]]] = {}

    def setnx(self, k, v):
        if k in self._d:
            return False
        self._d[k] = v
        return True

    def expire(self, k, ttl):
        return k in self._d

    def zadd(self, k, mapping):
        self._zset.setdefault(k, [])
        for m, s in mapping.items():
            self._zset[k].append((s, m))
        return len(mapping)

    def zcard(self, k):
        return len(self._zset.get(k, []))

    def zpopmin(self, k, count: int = 1):
        items = self._zset.get(k, [])
        if not items:
            return []
        items.sort(key=lambda x: x[0])
        popped = items[:count]
        self._zset[k] = items[count:]
        return [(m, s) for s, m in popped]


class RateLimiter:
    def __init__(self, config: Optional[dict] = None,
                 redis_client: Any = None, clock: Any = None) -> None:
        self._cfg = config or _load_config()
        self._redis = redis_client or _InMemoryRedis()
        self._clock = clock or time.time
        comp = self._cfg["complaint"]
        self._max_active = comp["max_active"]
        self._queue_max = comp["queue_max"]
        self._lock_ttl = comp.get("lock_ttl_seconds", TTL_COMPLAINT_LOCK)
        self._queue_timeout = comp.get("queue_timeout_minutes", 10) * 60
        self._active = 0
        qcfg = self._cfg.get("queue", {})
        self._dynamic = bool(qcfg.get("dynamic_consume", True))
        self._base_rate = int(qcfg.get("base_consume_rate", 5))
        self._mcp_buckets = {k: _TokenBucket(v)
                             for k, v in self._cfg["mcp"]["qps"].items()}
        self._llm_buckets = {k: _TokenBucket(v)
                             for k, v in self._cfg["llm"]["qps"].items()}

    def acquire_complaint(self, complaint_id: str) -> bool:
        """维5 单工单幂等锁 + 维1 并发 Semaphore。"""
        lock_key = complaint_lock_key(complaint_id)
        if not self._redis.setnx(lock_key, "1"):
            return False
        self._redis.expire(lock_key, self._lock_ttl)
        if self._active >= self._max_active:
            if self._redis.zcard(QUEUE_KEY) >= self._queue_max:
                return False
            self._redis.zadd(QUEUE_KEY, {complaint_id: self._clock()})
            return False
        self._active += 1
        return True

    def release_complaint(self) -> None:
        if self._active > 0:
            self._active -= 1

    def consume_pending(self, max_n: int | None = None) -> list[str]:
        """维4：高投诉涌入缓冲——按动态速率消费 pending 队列。"""
        if not self._dynamic:
            return []
        n = max_n if max_n is not None else self._base_rate
        # 积压越大，消费越快（上限 base*3）
        depth = self._redis.zcard(QUEUE_KEY)
        if depth > self._queue_max // 2:
            n = min(n * 3, self._base_rate * 3)
        elif depth > self._queue_max // 5:
            n = min(n * 2, self._base_rate * 2)
        now = self._clock()
        raw = self._redis.zpopmin(QUEUE_KEY, count=n)
        out: list[str] = []
        for member, score in raw:
            # 超时丢弃
            if now - float(score) > self._queue_timeout:
                continue
            if self._active < self._max_active:
                self._active += 1
                out.append(member)
            else:
                # 仍满：退回队列
                self._redis.zadd(QUEUE_KEY, {member: score})
        return out

    def acquire_mcp(self, server: str) -> bool:
        bucket = self._mcp_buckets.get(server)
        if bucket is None:
            return True
        return bucket.acquire()

    def acquire_llm(self, model: str) -> bool:
        bucket = self._llm_buckets.get(model)
        if bucket is None:
            return True
        return bucket.acquire()

    @property
    def config(self) -> dict:
        return self._cfg


rate_limiter = RateLimiter()

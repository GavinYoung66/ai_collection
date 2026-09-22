"""Redis 键与 qc:job 任务态。status 仅 WAIT_TEXT|RUNNING|DONE；TTL 24h；DB 3。"""
from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID

from redis.asyncio import Redis

from common.resilience.breaker import make_llm_breaker_key

JobStatus = Literal["WAIT_TEXT", "RUNNING", "DONE"]

JOB_TTL_SECONDS = 24 * 3600
ALLOWED_JOB_STATUS: frozenset[str] = frozenset({"WAIT_TEXT", "RUNNING", "DONE"})
FORBIDDEN_JOB_STATUS: frozenset[str] = frozenset({"PENDING_REVIEW", "REVIEWED", "PARTIAL", "FAILED"})

KEY_WORDS_VERSION = "qc:words:version"
KEY_RULES_ACTIVE = "qc:rules:active"
LLM_SERVICE = "qc"


def llm_degraded_key(model: str) -> str:
    return make_llm_breaker_key(LLM_SERVICE, model)


def llm_chain_models() -> list[str]:
    from app.core.config import qc_settings

    models: list[str] = []
    for item in [qc_settings.QC_LLM_MODEL, *(qc_settings.QC_LLM_FALLBACK_CHAIN or [])]:
        name = (item or "").strip()
        if name and name not in models:
            models.append(name)
    return models


def is_llm_chain_open_in_process() -> bool:
    """不读 Redis：配置链每个模型的进程内 breaker 均为 open 才为 true。"""
    from common.llm.reliability import _llm_breakers

    models = llm_chain_models()
    if not models:
        return False
    for model in models:
        breaker = _llm_breakers.get(llm_degraded_key(model))
        if breaker is None or breaker.state != "open":
            return False
    return True


def job_key(session_id: str) -> str:
    return f"qc:job:{session_id}"


def wait_retry_key(session_id: str) -> str:
    return f"qc:job:{session_id}:wait_retries"


def replay_run_key(session_id: str) -> str:
    return f"qc:replay:run:{session_id}"


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _load(raw: str | bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


class QcJobStore:
    """qc:job 与辅助键。WAIT_TEXT 只写 Redis，调用方 MUST NOT INSERT qc_records。"""

    def __init__(self, redis: Redis):
        self._redis = redis

    def _assert_db3(self) -> None:
        db = self._redis.connection_pool.connection_kwargs.get("db")
        if db != 3:
            raise RuntimeError(f"qc job store must use Redis DB 3, got {db!r}")

    async def set_job(
        self,
        session_id: str,
        *,
        status: JobStatus,
        qc_record_id: UUID | str | None = None,
        skipped_rules: list[str] | None = None,
    ) -> dict[str, Any]:
        self._assert_db3()
        if status not in ALLOWED_JOB_STATUS or status in FORBIDDEN_JOB_STATUS:
            raise ValueError(f"Redis job.status must be WAIT_TEXT|RUNNING|DONE, got {status!r}")
        payload: dict[str, Any] = {
            "status": status,
            "qc_record_id": str(qc_record_id) if qc_record_id is not None else None,
            "skipped_rules": list(skipped_rules or []),
        }
        await self._redis.set(job_key(session_id), _dump(payload), ex=JOB_TTL_SECONDS)
        return payload

    async def set_wait_text(self, session_id: str) -> dict[str, Any]:
        return await self.set_job(session_id, status="WAIT_TEXT", qc_record_id=None, skipped_rules=[])

    async def set_running(self, session_id: str) -> dict[str, Any]:
        return await self.set_job(session_id, status="RUNNING")

    async def set_done(
        self,
        session_id: str,
        *,
        qc_record_id: UUID | str,
        skipped_rules: list[str] | None = None,
    ) -> dict[str, Any]:
        return await self.set_job(
            session_id,
            status="DONE",
            qc_record_id=qc_record_id,
            skipped_rules=skipped_rules or [],
        )

    async def get_job(self, session_id: str) -> dict[str, Any] | None:
        self._assert_db3()
        return _load(await self._redis.get(job_key(session_id)))

    async def get_wait_retries(self, session_id: str) -> int:
        self._assert_db3()
        raw = await self._redis.get(wait_retry_key(session_id))
        if raw is None:
            return 0
        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        try:
            return int(text)
        except ValueError:
            return 0

    async def incr_wait_retries(self, session_id: str) -> int:
        self._assert_db3()
        key = wait_retry_key(session_id)
        incr = getattr(self._redis, "incr", None)
        if incr is not None:
            n = await incr(key)
            expire = getattr(self._redis, "expire", None)
            if expire is not None:
                await expire(key, JOB_TTL_SECONDS)
            return int(n)
        current = await self.get_wait_retries(session_id) + 1
        await self._redis.set(key, str(current), ex=JOB_TTL_SECONDS)
        return current

    async def delete_job(self, session_id: str) -> None:
        self._assert_db3()
        await self._redis.delete(job_key(session_id))

    async def set_words_version(self, version: str) -> None:
        self._assert_db3()
        await self._redis.set(KEY_WORDS_VERSION, version)

    async def get_words_version(self) -> str | None:
        self._assert_db3()
        raw = await self._redis.get(KEY_WORDS_VERSION)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def set_rules_active(self, version: str) -> None:
        self._assert_db3()
        await self._redis.set(KEY_RULES_ACTIVE, version)

    async def get_rules_active(self) -> str | None:
        self._assert_db3()
        raw = await self._redis.get(KEY_RULES_ACTIVE)
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def set_replay_run(self, session_id: str, token: str) -> None:
        self._assert_db3()
        await self._redis.set(replay_run_key(session_id), token)

    async def get_replay_run(self, session_id: str) -> str | None:
        self._assert_db3()
        raw = await self._redis.get(replay_run_key(session_id))
        if raw is None:
            return None
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def is_llm_chain_degraded(self) -> bool:
        """配置链全部 open 才为 true（Redis 分片键或进程内 breaker）。"""
        from common.llm.reliability import _llm_breakers

        self._assert_db3()
        models = llm_chain_models()
        if not models:
            return False
        for model in models:
            key = llm_degraded_key(model)
            raw = await self._redis.get(key)
            if raw is not None:
                continue
            breaker = _llm_breakers.get(key)
            if breaker is not None and breaker.state == "open":
                continue
            return False
        return True

"""QC-2.3：Redis qc:job DB 3 读写；WAIT_TEXT 不插表。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.models.qc import QcRecord
from app.core.config import qc_settings
from app.repositories.job_store import (
    FORBIDDEN_JOB_STATUS,
    JOB_TTL_SECONDS,
    KEY_RULES_ACTIVE,
    KEY_WORDS_VERSION,
    QcJobStore,
    job_key,
    llm_degraded_key,
    replay_run_key,
)
from app.repositories.quality_repo import QualityRepo


class MemoryRedis:
    def __init__(self, db: int = 3):
        self.connection_pool = type("Pool", (), {"connection_kwargs": {"db": db}})()
        self.data: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int | None] = {}
        self.published: list[tuple[str, str]] = []
        self.xadd_log: list[tuple[str, dict]] = []

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False, **_kwargs):
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttl[key] = ex
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def delete(self, *keys: str):
        for key in keys:
            self.data.pop(key, None)
            self.ttl.pop(key, None)
            self.lists.pop(key, None)

    async def rpush(self, key: str, *values: str) -> int:
        bucket = self.lists.setdefault(key, [])
        bucket.extend(str(v) for v in values)
        return len(bucket)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    async def incr(self, key: str) -> int:
        current = int(self.data.get(key) or 0) + 1
        self.data[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> None:
        self.ttl[key] = seconds

    async def exists(self, key: str) -> int:
        return 1 if key in self.data else 0

    async def setex(self, key: str, time: int, value: str):
        return await self.set(key, value, ex=time)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def xadd(self, name: str, fields: dict, id: str = "*") -> str:
        self.xadd_log.append((name, dict(fields)))
        return "1-0"


class TestQcJobStore:
    async def test_wait_text_roundtrip_db3_ttl_24h(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        payload = await store.set_wait_text("sess-1")
        assert payload["status"] == "WAIT_TEXT"
        assert payload["qc_record_id"] is None
        assert payload["skipped_rules"] == []
        raw = await redis.get(job_key("sess-1"))
        assert json.loads(raw)["status"] == "WAIT_TEXT"
        assert redis.ttl[job_key("sess-1")] == JOB_TTL_SECONDS == 86400
        loaded = await store.get_job("sess-1")
        assert loaded == payload

    async def test_done_is_not_pending_review(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        rid = uuid4()
        payload = await store.set_done("sess-2", qc_record_id=rid, skipped_rules=["R01"])
        assert payload["status"] == "DONE"
        assert payload["status"] not in FORBIDDEN_JOB_STATUS
        assert payload["qc_record_id"] == str(rid)
        assert payload["skipped_rules"] == ["R01"]

    async def test_rejects_db_statuses_as_job_status(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        with pytest.raises(ValueError):
            await store.set_job("sess-x", status="PENDING_REVIEW")  # type: ignore[arg-type]

    async def test_wrong_redis_db_rejected(self):
        store = QcJobStore(MemoryRedis(db=0))
        with pytest.raises(RuntimeError):
            await store.set_wait_text("sess-3")

    async def test_aux_keys_set_del_no_expire(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        await store.set_words_version("w1")
        await store.set_rules_active("v1.0")
        await store.set_replay_run("sess-4", "run-1")
        assert await store.get_words_version() == "w1"
        assert await store.get_rules_active() == "v1.0"
        assert await store.get_replay_run("sess-4") == "run-1"
        assert redis.ttl[KEY_WORDS_VERSION] is None
        assert redis.ttl[KEY_RULES_ACTIVE] is None
        assert redis.ttl[replay_run_key("sess-4")] is None
        assert "qc:llm_degraded" not in redis.data
        assert not hasattr(store, "set_llm_degraded")
        assert not hasattr(store, "get_llm_degraded")
        assert not hasattr(store, "clear_llm_degraded")

    async def test_llm_chain_degraded_requires_whole_chain(self):
        from common.llm import reliability as reliability_mod

        reliability_mod._llm_breakers.clear()
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        primary = llm_degraded_key(qc_settings.QC_LLM_MODEL)
        backup = llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])
        assert await store.is_llm_chain_degraded() is False
        redis.data[primary] = "1"
        assert await store.is_llm_chain_degraded() is False
        redis.data[backup] = "1"
        assert await store.is_llm_chain_degraded() is True
        assert "qc:llm_degraded" not in redis.data

    async def test_wait_text_does_not_insert_qc_records(self):
        class ExplodingSession:
            def add(self, obj):
                raise AssertionError("WAIT_TEXT must not INSERT qc_records")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        await store.set_wait_text("sess-wait")
        session = ExplodingSession()
        repo = QualityRepo(session, job_store=store)  # type: ignore[arg-type]
        assert repo.session is session
        with pytest.raises(ValueError):
            QcRecord.assert_db_status("WAIT_TEXT")

    async def test_live_redis_db3_roundtrip_or_skip(self):
        """能连则写入真实 Redis DB 3 并读回；连不上则 skip，禁止假连成功。"""
        from app.deps import build_redis

        client = build_redis()
        try:
            await client.ping()
        except Exception as exc:
            await client.aclose()
            pytest.skip(f"Redis unreachable: {exc}")
        sid = f"sess-live-{uuid4()}"
        rid = uuid4()
        store = QcJobStore(client)
        try:
            assert client.connection_pool.connection_kwargs["db"] == 3
            payload = await store.set_done(sid, qc_record_id=rid, skipped_rules=["R14"])
            loaded = await store.get_job(sid)
            assert loaded == payload
            assert loaded["status"] == "DONE"
            assert loaded["qc_record_id"] == str(rid)
            assert loaded["skipped_rules"] == ["R14"]
        finally:
            await store.delete_job(sid)
            assert await store.get_job(sid) is None
            await client.aclose()

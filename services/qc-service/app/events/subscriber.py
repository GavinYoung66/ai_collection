"""质检 Redis 订阅。可注入 handler；listen 循环仅进程后台使用，单测直接调 handler。"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import QcSettings, qc_settings
from app.deps import get_active_qc_rules, get_rag_client, get_redis
from app.events.publisher import FORBIDDEN_CHANNELS, QcEventPublisher
from app.integrations.dp_client import DpClient
from app.integrations.qc_session import QCSession
from app.repositories.job_store import QcJobStore
from app.repositories.quality_repo import QualityRepo
from app.services.ac_filter import get_shared_ac
from app.services.offline_qc_service import CODE_OK, OfflineQcService, QcRunResult, _text_ready
from app.services.replay_service import ReplayService
from app.services.word_loader import WordLoader
from common.db.session import async_session_factory

logger = logging.getLogger(__name__)

CHANNEL_SESSION_ENDED = "call.session.ended"
CHANNEL_ETL_DONE = "etl.task.done"
CHANNEL_KB_UPDATED = "kb.sensitive_updated"
CHANNEL_STRATEGY_UPDATED = "strategy.updated"

SUBSCRIBE_CHANNELS = (
    CHANNEL_SESSION_ENDED,
    CHANNEL_ETL_DONE,
    CHANNEL_KB_UPDATED,
    CHANNEL_STRATEGY_UPDATED,
)

ReplayFn = Callable[[QCSession], Awaitable[QcRunResult]]


def parse_event_payload(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("event payload is not JSON")
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def default_replay_session(qc: QCSession) -> QcRunResult:
    redis = await get_redis()
    job_store = QcJobStore(redis)
    publisher = QcEventPublisher(redis)
    async with async_session_factory() as session:
        repo = QualityRepo(session, job_store)
        offline = OfflineQcService(
            ac=get_shared_ac(),
            job_store=job_store,
            publisher=publisher,
        )
        replay = ReplayService(offline, quality_repo=repo)
        return await replay.replay_session(qc)


class QcEventSubscriber:
    """订阅四个频道。不消费 qc.escalate.human；不提供 DP 回调 HTTP。"""

    def __init__(
        self,
        *,
        dp: DpClient | None = None,
        replay: ReplayService | None = None,
        replay_session: ReplayFn | None = None,
        word_loader: WordLoader | None = None,
        job_store: QcJobStore | None = None,
        settings: QcSettings | None = None,
    ) -> None:
        self.dp = dp
        self.replay = replay
        self._replay_fn = replay_session
        self.word_loader = word_loader
        self.job_store = job_store
        self.settings = settings or qc_settings
        self.rules_cache = get_active_qc_rules()

    async def _do_replay(self, qc: QCSession) -> QcRunResult:
        if self._replay_fn is not None:
            return await self._replay_fn(qc)
        if self.replay is not None:
            return await self.replay.replay_session(qc)
        return await default_replay_session(qc)

    async def handle_message(self, channel: str, raw: Any) -> Any:
        name = channel.decode("utf-8") if isinstance(channel, (bytes, bytearray)) else str(channel)
        if name in FORBIDDEN_CHANNELS:
            logger.warning("refusing to consume forbidden channel %s", name)
            return {"status": "ignored", "channel": name}
        payload = parse_event_payload(raw)
        try:
            if name == CHANNEL_SESSION_ENDED:
                return await self.handle_session_ended(payload)
            if name == CHANNEL_ETL_DONE:
                return await self.handle_etl_done(payload)
            if name == CHANNEL_KB_UPDATED:
                return await self.handle_kb_updated(payload)
            if name == CHANNEL_STRATEGY_UPDATED:
                return await self.handle_strategy_updated(payload)
            logger.info("unsubscribed channel ignored: %s", name)
            return {"status": "ignored", "channel": name}
        except Exception:
            logger.exception("subscribe handler failed channel=%s", name)
            return {"status": "error", "channel": name}

    async def handle_session_ended(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id") or "")
        if not session_id:
            logger.warning("call.session.ended missing session_id")
            return {"status": "ignored", "reason": "missing_session_id"}
        if self.dp is None:
            logger.warning("call.session.ended skipped: no DpClient")
            return {"status": "error", "reason": "no_dp"}
        try:
            qc = await self.dp.get_qc_source(session_id)
        except Exception:
            logger.exception("call.session.ended get_qc_source failed session_id=%s", session_id)
            return {"status": "skipped", "reason": "qc_source_failed", "session_id": session_id}
        if not self.settings.QC_AUTO_START_ON_ENDED:
            if not _text_ready(qc) and self.job_store is not None:
                await self.job_store.set_wait_text(qc.session_id)
                return {
                    "status": "WAIT_TEXT",
                    "inserted": False,
                    "session_id": qc.session_id,
                    "reason": "auto_start_disabled",
                }
            return {"status": "skipped", "reason": "auto_start_disabled", "session_id": qc.session_id}
        result = await self._do_replay(qc)
        return {
            "status": result.job_status or result.qc_status or result.code,
            "inserted": bool(result.inserted),
            "session_id": qc.session_id,
            "code": result.code,
            "job_status": result.job_status,
        }

    async def handle_etl_done(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.QC_AUTO_COMPLEMENT_AFTER_ETL:
            return {"status": "skipped", "reason": "auto_complement_disabled", "complemented": []}
        if self.dp is None:
            logger.warning("etl.task.done skipped: no DpClient")
            return {"status": "error", "reason": "no_dp", "complemented": []}
        etl_date = payload.get("backfill_date")
        day = str(etl_date) if etl_date not in (None, "") else None
        ids = await self.dp.list_pending_session_ids(etl_date=day)
        if not ids:
            return {"status": "skipped", "reason": "empty_or_breaker", "complemented": []}
        complemented: list[str] = []
        skipped: list[str] = []
        for session_id in ids:
            try:
                qc = await self.dp.get_qc_source(session_id)
            except Exception:
                logger.exception(
                    "etl.task.done get_qc_source failed session_id=%s; skip, not complemented",
                    session_id,
                )
                skipped.append(session_id)
                continue
            try:
                result = await self._do_replay(qc)
            except Exception:
                logger.exception("etl.task.done replay failed session_id=%s", session_id)
                skipped.append(session_id)
                continue
            if result.inserted or (
                bool(result.qc_record_id)
                and result.code == CODE_OK
                and result.job_status != "WAIT_TEXT"
            ):
                complemented.append(session_id)
            else:
                skipped.append(session_id)
        return {
            "status": "ok",
            "complemented": complemented,
            "skipped": skipped,
        }

    async def handle_kb_updated(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.word_loader is None:
            logger.warning("kb.sensitive_updated skipped: no WordLoader")
            return {"status": "error", "reason": "no_word_loader"}
        loaded = await self.word_loader.on_kb_sensitive_updated(payload)
        return {"status": "ok" if loaded.get("ok") else "kept_old", **loaded}

    async def handle_strategy_updated(self, payload: dict[str, Any]) -> dict[str, Any]:
        """读 active qc_rules 快照并写 qc:rules:active。不实现 POST /qc/rules/apply。"""
        snap = get_active_qc_rules()
        self.rules_cache = snap
        version = payload.get("version")
        key = str(version) if version not in (None, "") else str(snap.get("version") or "")
        if self.job_store is not None and key:
            await self.job_store.set_rules_active(key)
        return {"status": "ok", "version": key, "strategy_type": payload.get("strategy_type")}

    async def listen(self, redis: Any) -> None:
        pubsub = redis.pubsub()
        await pubsub.subscribe(*SUBSCRIBE_CHANNELS)
        try:
            async for message in pubsub.listen():
                if not message or message.get("type") != "message":
                    continue
                try:
                    await self.handle_message(message.get("channel"), message.get("data"))
                except Exception:
                    logger.exception("subscribe dispatch failed")
        finally:
            close = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if close is not None:
                try:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("pubsub close failed")

    async def listen_forever(self, redis: Any) -> None:
        while True:
            try:
                await self.listen(redis)
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscribe loop failed; retry")
                await asyncio.sleep(1.0)


def skip_background_tasks() -> bool:
    import os

    if os.environ.get("QC_SKIP_BACKGROUND") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


async def start_background_tasks() -> list[asyncio.Task[Any]]:
    """启动 WordLoader + 订阅。失败只记日志，不抛给 FastAPI。"""
    if skip_background_tasks():
        return []
    tasks: list[asyncio.Task[Any]] = []
    try:
        redis = await get_redis()
        job_store = QcJobStore(redis)
        loader = WordLoader(rag=get_rag_client(), ac=get_shared_ac(), job_store=job_store)
        try:
            await loader.load_once()
        except Exception:
            logger.exception("startup WordLoader.load_once failed; keep empty/old AC")
        subscriber = QcEventSubscriber(
            dp=DpClient(),
            word_loader=loader,
            job_store=job_store,
        )

        async def _listen() -> None:
            try:
                await subscriber.listen_forever(redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscribe background task died")

        async def _hourly() -> None:
            try:
                await loader.run_hourly()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hourly WordLoader task died")

        tasks.append(asyncio.create_task(_listen(), name="qc-subscribe"))
        tasks.append(asyncio.create_task(_hourly(), name="qc-words-hourly"))
    except Exception:
        logger.exception("qc background start failed")
    return tasks

"""词库全量加载：启动 / 每小时 / kb.sensitive_updated。禁止 since_v 与 /kb/search。"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import qc_settings
from app.integrations.rag_client import RagClient
from app.observability import emit_words_loaded, evaluate_words_stale
from app.repositories.job_store import QcJobStore
from app.services.ac_filter import ACFilter, SensitiveWord, get_shared_ac

logger = logging.getLogger(__name__)

HOURLY_INTERVAL_SECONDS = 3600.0


def _to_sensitive(words: list[str]) -> list[SensitiveWord]:
    return [SensitiveWord(word=item) for item in words if item]


class WordLoader:
    """GET current.dict → ACFilter.reload；失败保持旧 AC，不改 qc:words:version。"""

    def __init__(
        self,
        *,
        rag: RagClient | None = None,
        ac: ACFilter | None = None,
        job_store: QcJobStore | None = None,
        interval_seconds: float | None = None,
    ) -> None:
        self.rag = rag if rag is not None else RagClient()
        self.ac = ac if ac is not None else get_shared_ac()
        self.job_store = job_store
        self.interval_seconds = (
            float(interval_seconds) if interval_seconds is not None else HOURLY_INTERVAL_SECONDS
        )
        self._cron = qc_settings.QC_RAG_HOURLY_CRON

    async def load_once(self, *, event_version: str | None = None) -> dict[str, Any]:
        current = await self._current_version()
        if self.rag.breaker_open:
            evaluate_words_stale(
                fetch_failing=True,
                qc_version=current,
                rag_version=event_version,
            )
            return {"ok": False, "kept_old": True, "reason": "breaker_open", "version": current}
        words = await self.rag.fetch_current_dict()
        if not self.rag.fetch_healthy:
            evaluate_words_stale(
                fetch_failing=True,
                qc_version=current,
                rag_version=event_version,
            )
            return {"ok": False, "kept_old": True, "reason": "fetch_failed", "version": current}
        await self.ac.reload(_to_sensitive(words))
        version = str(event_version) if event_version not in (None, "") else f"full:{len(words)}"
        if self.job_store is not None:
            await self.job_store.set_words_version(version)
        emit_words_loaded(version=version, word_count=len(words))
        return {"ok": True, "kept_old": False, "version": version, "word_count": len(words)}

    async def on_kb_sensitive_updated(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = payload.get("version")
        return await self.load_once(event_version=str(version) if version is not None else None)

    async def run_hourly(self, *, stop: asyncio.Event | None = None) -> None:
        """对齐 QC_RAG_HOURLY_CRON：等价每小时循环，不引入 cron 框架。"""
        while True:
            try:
                if stop is not None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
                        return
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                raise
            if stop is not None and stop.is_set():
                return
            try:
                await self.load_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("hourly current.dict reload failed; keep old AC")

    async def _current_version(self) -> str | None:
        if self.job_store is None:
            return None
        return await self.job_store.get_words_version()

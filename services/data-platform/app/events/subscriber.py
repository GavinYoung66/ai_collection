"""qc.check.completed 订阅器（data-platform 事件链核心）。

收到 qc.check.completed 且 case_id 匹配种子案件后：
1. 更新快照质检列（5.1）
2. HTTP POST /api/v1/complaints 创建工单（3.2，只带 complaint_text + case_id）
3. POST /api/v1/complaints/{id}/judge run_graph=true（3.5，停在人审）
4. PUT /api/v1/complaints/{id}/review approve（3.5，恢复图进入 sample_backflow）

禁止：
- 轮询/sleep 等待 qc 结果
- 使用 qc.complaint.suspected 自动建单（3.6）
- HTTP POST 调用 qc-service（只用 Redis 事件）
- 事件进 0 号库
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.core.config import dp_settings
from app.core.redis import get_redis
from app.integrations.complaint_client import ComplaintClient
from app.services.snapshot import (
    check_has_high_alert,
    qc_record_exists,
    update_qc_cols,
)

logger = logging.getLogger(__name__)

CHANNEL_QC_CHECK_COMPLETED = "qc.check.completed"
CHANNEL_QC_COMPLAINT_SUSPECTED = "qc.complaint.suspected"  # 3.6: 不订阅此频道建单

# 种子案件固定 UUID（与 seed_min_loop.py 对齐）；闭环只处理匹配该 case_id 的事件。
SEED_CASE_ID = "11111111-2222-4333-8444-555555555501"

# 超时看门狗本地检查间隔（秒）。仅检查内存计时表，不轮询 qc 结果。
_WATCHDOG_INTERVAL_SECONDS = 30.0

# 默认投诉正文（人审前 AI 初判参考）
_DEFAULT_COMPLAINT_TEXT = "质检发现坐席通话中存在不当话术（假冒法院、威胁催收），请研判是否构成违规投诉。"


def parse_event_payload(raw: Any) -> dict[str, Any]:
    """解析 Redis pubsub 消息 payload。"""
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
            logger.warning("qc.check.completed payload is not JSON")
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


async def _update_snapshot_qc_cols(case_id: str, payload: dict[str, Any]) -> bool:
    """5.1: 用 payload 更新快照质检列。

    事件键映射：score→compliance_score、risk→risk_level、grade 不落库。
    has_high_alert 查该 session_id 的 qc_alerts：有 level=high 则为 '1'，否则 '0'。
    """
    from common.db.session import async_session_factory

    session_id = str(payload.get("session_id") or "")
    qc_record_id = payload.get("qc_record_id")
    score = payload.get("score")  # 事件键 score → 库列 compliance_score
    risk = payload.get("risk")    # 事件键 risk → 库列 risk_level
    # grade 不落库

    # compliance_score: int | None
    compliance_score: int | None = None
    if score is not None:
        try:
            compliance_score = int(score)
        except (ValueError, TypeError):
            compliance_score = None

    risk_level: str | None = str(risk) if risk is not None else None

    # has_high_alert: 查 qc_alerts 是否含 level=high
    has_high = "0"
    if session_id:
        try:
            async with async_session_factory() as session:
                high = await check_has_high_alert(session, session_id)
                has_high = "1" if high else "0"
        except Exception:
            logger.exception("check_has_high_alert failed", extra={"session_id": session_id})

    try:
        async with async_session_factory() as session:
            updated = await update_qc_cols(
                session,
                case_id,
                qc_record_id=str(qc_record_id) if qc_record_id else None,
                compliance_score=compliance_score,
                risk_level=risk_level,
                has_high_alert=has_high,
            )
            if updated:
                await session.commit()
                logger.info(
                    "snapshot_qc_cols_updated",
                    extra={
                        "case_id": case_id,
                        "session_id": session_id,
                        "compliance_score": compliance_score,
                        "risk_level": risk_level,
                        "has_high_alert": has_high,
                    },
                )
            else:
                logger.warning(
                    "snapshot_qc_cols_not_found",
                    extra={"case_id": case_id},
                )
            return updated
    except Exception:
        logger.exception("update_snapshot_qc_cols failed", extra={"case_id": case_id})
        return False


async def _confirm_qc_record(session_id: str) -> bool:
    """推投诉前确认 qc_records 已落库（真开检，非手插）。"""
    from common.db.session import async_session_factory

    if not session_id:
        return False
    try:
        async with async_session_factory() as session:
            return await qc_record_exists(session, session_id)
    except Exception:
        logger.exception("qc_record_exists check failed", extra={"session_id": session_id})
        return False


async def handle_qc_check_completed(payload: dict[str, Any]) -> dict[str, Any]:
    """收到 qc.check.completed 后的事件链处理。

    1. 仅处理匹配种子 case_id 的事件
    2. 确认 qc_records 已落库（真开检）
    3. 更新快照质检列（5.1）
    4. 创建投诉工单（3.2，只带 complaint_text + case_id）
    5. POST judge run_graph=true（3.5）
    6. PUT review approve（3.5）
    """
    case_id = str(payload.get("case_id") or "")
    session_id = str(payload.get("session_id") or "")

    if not case_id:
        logger.warning("qc.check.completed missing case_id, skip")
        return {"status": "skipped", "reason": "missing_case_id"}

    # 闭环只处理种子案件；其它 case_id 的事件记录但不触发建单
    if case_id != SEED_CASE_ID:
        logger.info(
            "qc.check.completed ignored (case_id not in loop scope)",
            extra={"case_id": case_id, "session_id": session_id},
        )
        return {"status": "skipped", "reason": "case_id_mismatch", "case_id": case_id}

    logger.info(
        "qc.check.completed received",
        extra={
            "case_id": case_id,
            "session_id": session_id,
            "qc_record_id": payload.get("qc_record_id"),
            "score": payload.get("score"),
            "risk": payload.get("risk"),
        },
    )

    # 确认 qc_records 已落库后才推投诉（真开检，非手插）；不轮询，事件即触发一次确认
    if not await _confirm_qc_record(session_id):
        logger.error(
            "qc_record_not_found_skip_complaint",
            extra={"case_id": case_id, "session_id": session_id},
        )
        return {"status": "skipped", "reason": "qc_record_not_found", "case_id": case_id}

    # 5.1: 更新快照质检列
    await _update_snapshot_qc_cols(case_id, payload)

    # 3.2: 创建投诉工单（只带 complaint_text + case_id，不传 session_id）
    client = ComplaintClient()
    complaint_id = await client.create_complaint(
        case_id=case_id,
        complaint_text=_DEFAULT_COMPLAINT_TEXT,
    )
    if not complaint_id:
        logger.error(
            "complaint_create_failed",
            extra={"case_id": case_id},
        )
        return {"status": "error", "reason": "complaint_create_failed", "case_id": case_id}

    # 3.5: POST judge run_graph=true — 图停在 human_review
    await client.judge_complaint(complaint_id=complaint_id)

    # 3.5: PUT review approve — 恢复图线程，进入 sample_backflow → write_session
    await client.review_approve(complaint_id=complaint_id)

    return {
        "status": "ok",
        "case_id": case_id,
        "complaint_id": complaint_id,
    }


class QcCheckSubscriber:
    """订阅 qc.check.completed（走 QC_REDIS_DB，默认 3）。

    不订阅 qc.complaint.suspected（3.6：该事件仅用于已在途投诉补证，不触发建单）。
    不轮询、不 sleep 等待。
    """

    def __init__(
        self,
        *,
        handler=None,
        settings=dp_settings,
    ) -> None:
        self._handler = handler or handle_qc_check_completed
        self._settings = settings
        # 超时告警追踪：种子入库后开始计时
        self._seed_times: dict[str, float] = {}

    def track_seed(self, case_id: str, session_id: str) -> None:
        """3.7: 记录种子入库时间，用于超时告警。不阻塞、不 sleep。"""
        self._seed_times[case_id] = time.time()
        logger.info(
            "seed_tracked_for_timeout",
            extra={"case_id": case_id, "session_id": session_id},
        )

    def check_timeouts(self) -> list[dict[str, Any]]:
        """3.7: 检查超时——种子入库后 N 分钟未收到匹配 qc.check.completed。

        记告警日志（含 case_id、等待时长），不无限阻塞、不 sleep 轮询。
        返回超时的 case_id 列表。
        """
        timeout_seconds = self._settings.QC_TIMEOUT_MINUTES * 60
        now = time.time()
        timed_out: list[dict[str, Any]] = []
        for case_id, seed_time in list(self._seed_times.items()):
            elapsed = now - seed_time
            if elapsed >= timeout_seconds:
                wait_minutes = int(elapsed / 60)
                logger.warning(
                    "qc_check_completed_timeout",
                    extra={
                        "case_id": case_id,
                        "wait_minutes": wait_minutes,
                        "timeout_minutes": self._settings.QC_TIMEOUT_MINUTES,
                    },
                )
                timed_out.append({
                    "case_id": case_id,
                    "wait_minutes": wait_minutes,
                })
                # 超时后从追踪列表移除，不无限阻塞
                del self._seed_times[case_id]
        return timed_out

    def _clear_tracked(self, case_id: str) -> None:
        """收到匹配事件后清除追踪。"""
        self._seed_times.pop(case_id, None)

    async def handle_message(self, channel: str, raw: Any) -> Any:
        name = channel.decode("utf-8") if isinstance(channel, (bytes, bytearray)) else str(channel)

        # 3.6: qc.complaint.suspected 不触发建单，忽略
        if name == CHANNEL_QC_COMPLAINT_SUSPECTED:
            logger.info("qc.complaint.suspected ignored (not for auto-create)")
            return {"status": "ignored", "channel": name}

        if name != CHANNEL_QC_CHECK_COMPLETED:
            logger.info("unsubscribed channel ignored: %s", name)
            return {"status": "ignored", "channel": name}

        payload = parse_event_payload(raw)
        case_id = str(payload.get("case_id") or "")

        # 清除超时追踪（收到了匹配事件）
        if case_id:
            self._clear_tracked(case_id)

        try:
            return await self._handler(payload)
        except Exception:
            logger.exception("qc.check.completed handler failed")
            return {"status": "error", "case_id": case_id}

    async def listen(self, redis: Any) -> None:
        """订阅 qc.check.completed，循环消费。"""
        pubsub = redis.pubsub()
        await pubsub.subscribe(CHANNEL_QC_CHECK_COMPLETED)
        try:
            async for message in pubsub.listen():
                if not message or message.get("type") != "message":
                    continue
                try:
                    await self.handle_message(message.get("channel"), message.get("data"))
                except Exception:
                    logger.exception("subscribe dispatch failed")
                # 每轮消息后检查超时
                self.check_timeouts()
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

    async def watchdog_loop(self) -> None:
        """3.7: 独立定时看门狗。

        pubsub.listen() 在无消息时会永久阻塞，超时检查不能挂在消息循环里；
        本循环只按本地时间表检查，不查 qc、不 sleep 轮询质检结果。
        """
        while True:
            try:
                await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
                self.check_timeouts()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("timeout watchdog loop failed; continue")


# 进程级单例：供 run_min_loop / 发布链路调 track_seed，与后台订阅共用同一计时表。
_subscriber_instance: "QcCheckSubscriber | None" = None


def get_subscriber() -> QcCheckSubscriber:
    global _subscriber_instance
    if _subscriber_instance is None:
        _subscriber_instance = QcCheckSubscriber()
    return _subscriber_instance


def skip_background_tasks() -> bool:
    import os
    if os.environ.get("DP_SKIP_BACKGROUND") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


async def start_background_tasks() -> list[asyncio.Task[Any]]:
    """启动 qc.check.completed 订阅 + 超时看门狗。失败只记日志，不抛给 FastAPI。"""
    if skip_background_tasks():
        return []
    tasks: list[asyncio.Task[Any]] = []
    try:
        redis = await get_redis()
        subscriber = get_subscriber()

        async def _listen() -> None:
            try:
                await subscriber.listen_forever(redis)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("subscribe background task died")

        tasks.append(asyncio.create_task(_listen(), name="dp-qc-subscribe"))
        tasks.append(
            asyncio.create_task(subscriber.watchdog_loop(), name="dp-qc-timeout-watchdog")
        )
    except Exception:
        logger.exception("dp background start failed")
    return tasks

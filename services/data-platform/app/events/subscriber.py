"""qc.check.completed 订阅器（data-platform 事件链核心）。

收到 qc.check.completed（任意 case_id）后：
1. 确认 qc_records 已落库（真开检）
2. 更新快照质检列
3. HTTP POST /api/v1/complaints 创建工单（只带 complaint_text + case_id）
   - complaint_text 按各案实际违规项差异化组装（G3）
4. POST /api/v1/complaints/{id}/judge run_graph=true（停在人审）
   —— 仅 JUDGE_BUDGET 有界子集执行，其余停在"已创建未研判"
5. PUT /api/v1/complaints/{id}/review approve（恢复图进入 sample_backflow）
   —— 与 judge 同预算

G2：泛化处理全部 case_id，不再硬编码单个 SEED_CASE_ID。
保留 complaint-service 侧的 _graph_inflight / _resume_locks 在途守卫（本文件不触碰）。

禁止：
- 轮询/sleep 等待 qc 结果
- 使用 qc.complaint.suspected 自动建单
- HTTP POST 调用 qc-service（只用 Redis 事件）
- 事件进 0 号库
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from sqlalchemy import text

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
CHANNEL_QC_COMPLAINT_SUSPECTED = "qc.complaint.suspected"  # 不订阅此频道建单

# 超时看门狗本地检查间隔（秒）。仅检查内存计时表，不轮询 qc 结果。
_WATCHDOG_INTERVAL_SECONDS = 30.0

# ── judge 有界预算（G2 + N3 Redis 持久化 + N4 成功才消耗）──────────
# 投诉创建对所有 case_id 全量执行；judge/review 仅前 JUDGE_BUDGET 单跑图。
# 进程级缓存 + Redis Set 持久化（重启不重置）；Redis 故障降级到进程级。
_judged_count: int = 0
_judged_case_ids: set[str] = set()
_redis_synced: bool = False
_JUDGE_BUDGET_REDIS_KEY: str = "csmp:batch_traffic_loop:judged_case_ids"


async def _ensure_budget_synced() -> None:
    """N3：首次查询时从 Redis Set 加载已判定 case_id。Redis 故障降级到进程级。"""
    global _redis_synced, _judged_count
    if _redis_synced:
        return
    try:
        redis = await get_redis()
        case_ids = await redis.smembers(_JUDGE_BUDGET_REDIS_KEY)
        if case_ids:
            _judged_case_ids.update(case_ids)
            _judged_count = len(_judged_case_ids)
        _redis_synced = True
        logger.info("budget_synced_from_redis", extra={"count": _judged_count})
    except Exception:
        logger.warning(
            "budget_redis_sync_failed_fallback",
            extra={"key": _JUDGE_BUDGET_REDIS_KEY},
        )


async def _judge_budget_remaining() -> int:
    await _ensure_budget_synced()
    return max(0, dp_settings.JUDGE_BUDGET - _judged_count)


async def _mark_judged(case_id: str) -> None:
    """N3/N4：judge+review 成功后调用，同步 Redis Set。"""
    global _judged_count
    if case_id in _judged_case_ids:
        return
    _judged_case_ids.add(case_id)
    _judged_count += 1
    try:
        redis = await get_redis()
        await redis.sadd(_JUDGE_BUDGET_REDIS_KEY, case_id)
    except Exception:
        logger.warning(
            "budget_redis_write_failed",
            extra={"case_id": case_id, "key": _JUDGE_BUDGET_REDIS_KEY},
        )


# ── qc_violations 查询（G3 差异化正文）─────────────────────────
# qc_alerts 仅含 agent high 违规；要区分"客户侧异常"必须查 qc_violations 全量。
_SQL_VIOLATIONS_BY_SESSION = text(
    """
    SELECT v.rule_id, v.speaker, v.severity, v.redline_tag, v.evidence_text
    FROM qc_violations v
    JOIN qc_records q ON q.id = v.qc_record_id
    WHERE q.session_id = :session_id
      AND q.is_delete = '0'
      AND v.is_delete = '0'
    ORDER BY v.create_time
    """
)

# 快照行读 customer_name（脱敏）与 overdue_amount，用于正文槽位填充。
_SQL_SNAPSHOT_CTX = text(
    """
    SELECT customer_name, overdue_amount
    FROM dw.ads_case_loop
    WHERE case_id = :case_id
    """
)

# ── 正文模板（G3）────────────────────────────────────────────────
_TEMPLATE_AGENT_VIOLATION = (
    "质检发现坐席通话中存在违规话术（{violations}），"
    "客户{customer}欠款{overdue_amount}元，请研判是否构成违规投诉并采取相应处置。"
)
_TEMPLATE_USER_MISUNDERSTANDING = (
    "质检发现通话中客户侧存在异议（{violations}），"
    "客户{customer}欠款{overdue_amount}元，请研判客户诉求是否成立并跟进沟通。"
)
_TEMPLATE_COMPLIANT = (
    "质检未发现违规项，通话合规。客户{customer}欠款{overdue_amount}元，"
    "请按正常催收流程继续跟进。"
)
# 兜底正文（查询异常时使用，仍满足"只传 complaint_text + case_id"）
_FALLBACK_COMPLAINT_TEXT = "质检完成，请研判该通话是否构成违规投诉。"


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


# ── G3：差异化 complaint_text ──────────────────────────────────

# redline_tag → 中文违规项描述（用于正文摘要）。
# 键集合与 qc-service REDLINE_TAG_VALUES 对齐（8 项）；
# qc-service 落库时非白名单 tag 会被置 None，故 DB 只含这 8 项或 NULL。
# 防御：遇未知 tag 记 warn 提示补映射（N5），防止 qc-service 扩枚举后此处静默失真。
_REDLINE_LABEL: dict[str, str] = {
    "threat": "威胁恐吓",
    "fake_identity": "冒充身份",
    "stop_call": "违规骚扰",
    "fake_commit": "虚假承诺",
    "legal_overstate": "夸大法律后果",
    "unverified": "未核实信息",
    "data_fabrication": "数据造假",
    "privacy_leak": "隐私泄露",
}


async def _fetch_violations(session_id: str) -> list[dict[str, Any]]:
    """按 session_id 查 qc_violations 全量违规项（含 speaker/redline_tag/evidence_text）。

    qc_alerts 仅含 agent high，不足以区分"客户侧异常"，故查 qc_violations。
    """
    from common.db.session import async_session_factory

    if not session_id:
        return []
    try:
        async with async_session_factory() as session:
            result = await session.execute(_SQL_VIOLATIONS_BY_SESSION, {"session_id": session_id})
            rows = result.mappings().all()
            return [dict(r) for r in rows]
    except Exception:
        logger.exception("fetch_violations failed", extra={"session_id": session_id})
        return []


async def _fetch_case_context(case_id: str) -> tuple[str, str]:
    """从快照读 customer_name（脱敏）与 overdue_amount，用于正文槽位。"""
    from common.db.session import async_session_factory

    customer = "客户"
    overdue = "未知"
    try:
        async with async_session_factory() as session:
            result = await session.execute(_SQL_SNAPSHOT_CTX, {"case_id": case_id})
            row = result.mappings().first()
            if row:
                name = row.get("customer_name")
                if name:
                    customer = str(name)
                amt = row.get("overdue_amount")
                if amt is not None:
                    overdue = str(amt)
    except Exception:
        logger.exception("fetch_case_context failed", extra={"case_id": case_id})
    return customer, overdue


def _summarize_violations(violations: list[dict[str, Any]], speaker: str) -> str:
    """把指定 speaker 的违规项汇总为中文短语列表。未知 redline_tag 记 warn（N5）。"""
    seen: list[str] = []
    for v in violations:
        if str(v.get("speaker") or "") != speaker:
            continue
        tag = str(v.get("redline_tag") or "")
        if tag and tag not in _REDLINE_LABEL:
            logger.warning(
                "unknown_redline_tag",
                extra={"redline_tag": tag, "rule_id": v.get("rule_id"),
                       "hint": "qc-service 可能扩展了 REDLINE_TAG_VALUES，请补充 _REDLINE_LABEL 映射"},
            )
        label = _REDLINE_LABEL.get(tag, tag or "违规")
        if label not in seen:
            seen.append(label)
    if not seen:
        # 兜底用 rule_id
        for v in violations:
            if str(v.get("speaker") or "") != speaker:
                continue
            rid = str(v.get("rule_id") or "")
            if rid and rid not in seen:
                seen.append(rid)
    return "、".join(seen) if seen else "未明示违规项"


async def build_complaint_text(
    case_id: str,
    session_id: str,
    *,
    risk: Any = None,
    grade: Any = None,
) -> str:
    """按实际违规项组装差异化 complaint_text（G3）。

    分类（主信号：qc_violations 的 speaker）：
    - 有 agent 违规 → 坐席违规模板
    - 仅有 user 违规 → 客户侧异常模板
    - 无违规 → 干净合规模板
    辅助校验（N2）：payload 的 risk/grade 与违规分类交叉校验，不一致记 warn。
    """
    violations = await _fetch_violations(session_id)
    customer, overdue = await _fetch_case_context(case_id)

    agent_violations = [v for v in violations if str(v.get("speaker") or "") == "agent"]
    user_violations = [v for v in violations if str(v.get("speaker") or "") == "user"]

    if agent_violations:
        category = "agent_violation"
        summary = _summarize_violations(violations, "agent")
        text = _TEMPLATE_AGENT_VIOLATION.format(
            violations=summary, customer=customer, overdue_amount=overdue,
        )
    elif user_violations:
        category = "user_misunderstanding"
        summary = _summarize_violations(violations, "user")
        text = _TEMPLATE_USER_MISUNDERSTANDING.format(
            violations=summary, customer=customer, overdue_amount=overdue,
        )
    else:
        category = "compliant"
        text = _TEMPLATE_COMPLIANT.format(customer=customer, overdue_amount=overdue)

    # N2：risk/grade 与违规分类一致性校验（仅告警，不改分类结果）
    risk_s = str(risk or "").lower()
    if category == "compliant" and risk_s in ("high", "medium"):
        logger.warning(
            "complaint_text_risk_mismatch",
            extra={
                "case_id": case_id,
                "session_id": session_id,
                "category": category,
                "risk": risk,
                "grade": grade,
                "hint": "qc_violations 无违规但 payload risk 非 low，可能违规落库延迟或丢失",
            },
        )

    logger.info(
        "complaint_text_built",
        extra={
            "case_id": case_id,
            "session_id": session_id,
            "category": category,
            "agent_hits": len(agent_violations),
            "user_hits": len(user_violations),
            "risk": risk,
            "grade": grade,
        },
    )
    return text


async def handle_qc_check_completed(payload: dict[str, Any]) -> dict[str, Any]:
    """收到 qc.check.completed 后的事件链处理（G2 泛化 + G3 差异化正文）。

    1. 确认 qc_records 已落库（真开检）
    2. 更新快照质检列
    3. 创建投诉工单（全量 case_id，complaint_text 按违规项差异化）
    4. POST judge run_graph=true（仅 JUDGE_BUDGET 有界子集）
    5. PUT review approve（与 judge 同预算）
    """
    case_id = str(payload.get("case_id") or "")
    session_id = str(payload.get("session_id") or "")

    if not case_id:
        logger.warning("qc.check.completed missing case_id, skip")
        return {"status": "skipped", "reason": "missing_case_id"}

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

    # 更新快照质检列
    await _update_snapshot_qc_cols(case_id, payload)

    # 创建投诉工单（全量 case_id 必经；只带 complaint_text + case_id，不传 session_id）
    client = ComplaintClient()
    complaint_text = await build_complaint_text(
        case_id, session_id,
        risk=payload.get("risk"), grade=payload.get("grade"),
    )
    complaint_id = await client.create_complaint(
        case_id=case_id,
        complaint_text=complaint_text,
    )
    if not complaint_id:
        logger.error(
            "complaint_create_failed",
            extra={"case_id": case_id},
        )
        return {"status": "error", "reason": "complaint_create_failed", "case_id": case_id}

    # judge/review 受有界预算约束：仅前 JUDGE_BUDGET 单跑图，其余停在"已创建未研判"。
    # N4：judge+review 均成功后才消耗预算；judge 失败不消耗，留待下次事件重试。
    # 保留 complaint-service 侧 _graph_inflight / _resume_locks 在途守卫（本文件不触碰）。
    judged = False
    if case_id not in _judged_case_ids and (await _judge_budget_remaining()) > 0:
        # POST judge run_graph=true — 图停在 human_review
        judge_result = await client.judge_complaint(complaint_id=complaint_id)
        if not judge_result:
            logger.error(
                "complaint_judge_failed_budget_not_consumed",
                extra={"case_id": case_id, "complaint_id": complaint_id},
            )
        else:
            # PUT review approve — 恢复图线程，进入 sample_backflow → write_session
            review_result = await client.review_approve(complaint_id=complaint_id)
            if review_result:
                await _mark_judged(case_id)
                judged = True
            else:
                logger.error(
                    "complaint_review_failed_budget_not_consumed",
                    extra={"case_id": case_id, "complaint_id": complaint_id},
                )
    else:
        logger.info(
            "complaint_created_skip_judge",
            extra={
                "case_id": case_id,
                "complaint_id": complaint_id,
                "reason": "budget_exhausted" if (await _judge_budget_remaining()) == 0
                          else "already_judged",
                "judged_count": _judged_count,
                "budget": dp_settings.JUDGE_BUDGET,
            },
        )

    return {
        "status": "ok",
        "case_id": case_id,
        "complaint_id": complaint_id,
        "judged": judged,
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

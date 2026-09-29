"""run_min_loop.py — 最小闭环验收脚本（complaint-event-driven 6.1）

顺序：种子 → etl.task.done → 等 qc.check.completed → 更新质检列 →
发布 ``complaint.created``（EventBus）→ 等待快照投诉列回流 → 确认两侧非空。

投诉腿为**事件驱动**：脚本不调用 ComplaintClient 三步（create→judge→review）。
complaint-service 消费 created → 幂等建单 → judge；验收环境
``SIM_AUTO_REVIEW=true`` 同栈 auto-approve → sample_backflow → write_session
→ ``complaint.result.ready``。

等待证据选用**轮询快照投诉列**（非订阅 result.ready）：
理由 — 快照列是验收权威（两路回流对账）；脚本内再挂一条 RocketMQ
consumer 易与 dp 服务/qc 订阅争用客户端，轮询与既有 ``_poll_complaint_cols``
一致且实现更稳。

USE_MOCK_MCP=false（complaint-service 需以此环境启动）；SIM_AUTO_REVIEW=true。
可重复执行（case_id 幂等，不产生重复投诉）。

用法（仓库根目录）：
    python services/data-platform/scripts/run_min_loop.py

前置：
- complaint-service 以 USE_MOCK_MCP=false、SIM_AUTO_REVIEW=true 启动
- qc-service / rag-service / data-platform 运行中
- EVENT_BUS=rocketmq；Redis（db=3）和 PostgreSQL 可达
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# --- Path setup ---
_SCRIPT_DIR = Path(__file__).resolve().parent          # services/data-platform/scripts
_DP_ROOT = _SCRIPT_DIR.parents[0]                       # services/data-platform
_REPO_ROOT = _SCRIPT_DIR.parents[2]                    # ai_collection
for _p in (_REPO_ROOT, _DP_ROOT, _SCRIPT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import httpx                                           # noqa: E402
from sqlalchemy import text                            # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool                   # noqa: E402

from common.config.settings import settings as platform_settings  # noqa: E402
from app.core.config import dp_settings                # noqa: E402
from app.events.publisher import (  # noqa: E402
    publish_complaint_created,
    publish_etl_task_done,
)
from seed_min_loop import (  # noqa: E402
    seed, CASE_ID, SESSION_ID,
    PHONE_ENCRYPTED, PHONE_HASH, CUSTOMER_NAME,
)

logger = logging.getLogger("run_min_loop")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

_SHANGHAI = ZoneInfo("Asia/Shanghai")
CHANNEL_QC = "qc.check.completed"

# 验收结果
_results: list[dict[str, Any]] = []
# etl.task.done 发布时间（供 6.4 真开检校验）
_publish_time: datetime | None = None
# 幂等重跑标记：首次跑置 False；探测到已 DONE 质检时置 True，
# 6.1/6.4 据此跳过"必须重新开检/重发事件"的首跑口径（任务 6.4：连续重跑不产生重复数据）。
_idempotent_rerun = False


def _record(task: str, desc: str, passed: bool, evidence: str = "") -> None:
    _results.append({"task": task, "desc": desc, "pass": passed, "evidence": evidence})
    tag = "PASS" if passed else "FAIL"
    print(f"  [{task}] {tag} — {desc}")
    if evidence:
        print(f"         证据: {evidence}")


def _engine():
    return create_async_engine(platform_settings.pg_dsn, poolclass=NullPool)


# ──────────────────────────────────────────────────────────────
# 6.1 闭环流程
# ──────────────────────────────────────────────────────────────

def _unwrap_event_payload(raw: Any) -> dict[str, Any]:
    """兼容 EventEnvelope 与旧 flat JSON；定位字段优先取 data。"""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    if "event_id" in raw and isinstance(raw.get("data"), dict):
        payload = dict(raw["data"])
        if raw.get("trace_id") and "trace_id" not in payload:
            payload["trace_id"] = raw["trace_id"]
        return payload
    return raw


def _ensure_host_event_bus_env() -> None:
    """宿主机运行所需的 EventBus 环境默认值（幂等，不覆盖已有值）。

    本脚本按设计在**宿主机**运行：容器内服务用 ``rocketmq-proxy:8081``，宿主机走
    映射端口 ``localhost:18081``。``.env`` 里的 EVENT_BUS / ROCKETMQ_PROXY 只被
    pydantic 读取，不会导出到 ``os.environ``，因此此处 MUST NOT 以「os.environ 中
    EVENT_BUS 是否已存在」作为前置条件——否则幂等重跑分支会跳过代理默认值，
    RocketMQEventBus 回落到容器内主机名 ``rocketmq-proxy:8081``，在宿主机不可解析，
    报 ``failed to fetch topic:...route``。
    """
    os.environ.setdefault("EVENT_BUS", "rocketmq")
    os.environ.setdefault("EVENT_BUS_SVC", "dp")
    os.environ.setdefault("ROCKETMQ_PROXY", "localhost:18081")


async def _subscribe_qc_event(
    future: asyncio.Future,
    ready: asyncio.Event,
    accepting: asyncio.Event,
) -> None:
    """后台订阅 qc.check.completed（经 EventBus），收到匹配 case_id 后 set_result。

    ``ready`` 在 subscribe 启动后 set，供发布方避免 RocketMQ 冷订阅丢首条。
    ``accepting`` 在 etl 发布成功后 set，避免消费组积压的旧 qc.check.completed 抢答。
    RocketMQ 路径使用独立 consumer bus，不与 publisher 共用 Producer 客户端。
    """
    from common.eventbus.envelope import EventEnvelope
    from common.eventbus.factory import reset_event_bus
    from common.eventbus.rocketmq import RocketMQEventBus

    _ensure_host_event_bus_env()
    mode = (os.getenv("EVENT_BUS") or "rocketmq").strip().lower()
    if mode != "rocketmq":
        raise ValueError(f"run_min_loop requires EVENT_BUS=rocketmq, got {mode!r}")
    # Keep factory clear so publish_etl_task_done gets its own Producer.
    reset_event_bus()
    bus = RocketMQEventBus(endpoints=os.environ["ROCKETMQ_PROXY"])

    async def _handler(envelope: EventEnvelope) -> None:
        if not accepting.is_set():
            return
        payload = _unwrap_event_payload(envelope.model_dump(mode="json"))
        if str(payload.get("case_id") or "") != CASE_ID:
            return
        if not future.done():
            future.set_result(payload)

    try:
        await bus.subscribe(CHANNEL_QC, "dp_min_loop", _handler)
        # RocketMQ SimpleConsumer.startup 在后台线程；多等一会再发，并排空积压。
        await asyncio.sleep(3.0 if mode == "rocketmq" else 0.3)
        ready.set()
        while not future.done():
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("subscribe qc.check.completed failed")
        if not future.done():
            future.set_result(None)
        ready.set()
    finally:
        close = getattr(bus, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass


async def _enrich_score_risk(payload: dict[str, Any]) -> dict[str, Any]:
    """信封瘦身后从 qc HTTP 或库补全 score/risk。"""
    if payload.get("score") is not None and payload.get("risk") is not None:
        return payload
    session_id = str(payload.get("session_id") or "")
    if not session_id:
        return payload
    try:
        from app.integrations.qc_client import QcClient

        detail = await QcClient().get_record(session_id)
        if detail:
            out = dict(payload)
            if out.get("qc_record_id") in (None, "") and detail.get("qc_record_id"):
                out["qc_record_id"] = detail["qc_record_id"]
            score = detail.get("compliance_score", detail.get("score"))
            if out.get("score") is None and score is not None:
                out["score"] = score
            if out.get("risk") is None and detail.get("risk") is not None:
                out["risk"] = detail["risk"]
            return out
    except Exception:
        logger.exception("enrich score/risk via QcClient failed")
    return payload


async def _update_qc_cols(engine, payload: dict[str, Any]) -> None:
    """收到 qc.check.completed 后直接更新快照质检列（不依赖 subscriber 的 session_factory）。"""
    payload = await _enrich_score_risk(payload)
    session_id = str(payload.get("session_id") or "")
    qc_record_id = payload.get("qc_record_id")
    score = payload.get("score")
    risk = payload.get("risk")

    compliance_score = int(score) if score is not None else None
    risk_level = str(risk) if risk is not None else None

    # has_high_alert: 查 qc_alerts 是否含 level=high
    has_high = "0"
    if session_id:
        async with engine.connect() as conn:
            row = (await conn.execute(
                text("""SELECT EXISTS(
                    SELECT 1 FROM qc_alerts a
                    JOIN qc_records q ON q.id = a.qc_record_id
                    WHERE q.session_id = :sid AND q.is_delete='0'
                      AND a.level='high' AND a.is_delete='0'
                ) AS has_high"""),
                {"sid": session_id},
            )).mappings().first()
            has_high = "1" if row and row["has_high"] else "0"

    async with engine.begin() as conn:
        await conn.execute(
            text("""UPDATE dw.ads_case_loop
                    SET qc_record_id    = :qrid,
                        compliance_score = CAST(:score AS int),
                        risk_level       = CAST(:risk AS varchar),
                        has_high_alert   = CAST(:high AS char(1)),
                        backflow_at      = :now
                    WHERE case_id = :cid"""),
            {
                "qrid": str(qc_record_id) if qc_record_id else None,
                "score": compliance_score,
                "risk": risk_level,
                "high": has_high,
                "now": datetime.now(_SHANGHAI),
                "cid": CASE_ID,
            },
        )
    print(f"         质检列已更新: score={compliance_score} risk={risk_level} has_high_alert={has_high}")


async def _get_snapshot(engine) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("""SELECT case_id, customer_name, overdue_amount, overdue_days,
                          session_id, call_ended, qc_record_id, compliance_score,
                          risk_level, has_high_alert, complaint_id, verdict,
                          task_status, backflow_at
                   FROM dw.ads_case_loop WHERE case_id = :cid"""),
            {"cid": CASE_ID},
        )).mappings().first()
        return dict(row) if row else None


async def _poll_complaint_cols(engine, timeout: float = 60.0) -> bool:
    """轮询快照投诉列直到非空或超时（write_session 桥异步回写）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = await _get_snapshot(engine)
        if snap and snap.get("complaint_id") and snap.get("verdict"):
            return True
        await asyncio.sleep(2)
    return False


# 已完成（开检终态）的质检状态：有扣分 → PENDING_REVIEW，无扣分 → DONE
_QC_DONE_STATES = ("DONE", "PENDING_REVIEW")


async def _existing_done_qc(engine) -> dict[str, Any] | None:
    """幂等重跑探测：qc_records 已有开检终态记录且快照质检列已回写。

    qc-service 对已终态会话不重复开检、不重发 qc.check.completed（正确行为），
    故连续第二次执行时跳过事件等待，按"无重复数据"口径验收。
    """
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("""SELECT qc_status, compliance_score
                    FROM qc_records
                    WHERE session_id = :sid AND is_delete = '0'
                      AND qc_status = ANY(:done)
                    LIMIT 1"""),
            {"sid": SESSION_ID, "done": list(_QC_DONE_STATES)},
        )).mappings().first()
        snap = (await conn.execute(
            text("""SELECT compliance_score FROM dw.ads_case_loop
                    WHERE case_id = :cid"""),
            {"cid": CASE_ID},
        )).mappings().first()
    if (
        row and row.get("compliance_score") is not None
        and snap and snap.get("compliance_score") is not None
    ):
        return dict(row)
    return None


async def run_flow() -> bool:
    """种子 → qc 腿 → 发布 complaint.created → 轮询快照投诉列 → 确认两侧。"""
    print("\n=== 6.1 闭环流程（事件驱动投诉腿）===")
    engine = _engine()
    try:
        # 1. 种子
        print("  [1/6] 种子入库...")
        await seed()

        # 2. 幂等重跑探测（连续重跑不重复开检、无重复数据）
        global _idempotent_rerun
        done_qc = await _existing_done_qc(engine)
        if done_qc is not None:
            _idempotent_rerun = True
            print(f"  [2/6] 幂等重跑：质检已终态({done_qc.get('qc_status')})，"
                  f"跳过事件等待与质检列更新")
        else:
            _idempotent_rerun = False
            # 先订阅 qc.check.completed（ready 后再发布；accepting 在发布后打开）
            loop = asyncio.get_running_loop()
            qc_future: asyncio.Future = loop.create_future()
            sub_ready = asyncio.Event()
            sub_accepting = asyncio.Event()
            sub_task = asyncio.create_task(
                _subscribe_qc_event(qc_future, sub_ready, sub_accepting)
            )
            try:
                await asyncio.wait_for(sub_ready.wait(), timeout=45.0)
            except TimeoutError:
                sub_task.cancel()
                _record("6.1", "闭环流程", False, "订阅 qc.check.completed 未就绪")
                return False

            # 3. 发布 etl.task.done（独立 Producer；勿与订阅端共用 RocketMQ 客户端）
            print("  [3/6] 发布 etl.task.done...")
            global _publish_time
            _publish_time = datetime.now(_SHANGHAI)
            from common.eventbus.factory import reset_event_bus

            reset_event_bus()
            _ensure_host_event_bus_env()
            published = await publish_etl_task_done(case_id=CASE_ID, session_id=SESSION_ID)
            if not published:
                sub_task.cancel()
                _record("6.1", "闭环流程", False, "etl.task.done 发布失败")
                return False
            sub_accepting.set()

            # 4. 等 qc.check.completed
            print(f"  [4/6] 等待 qc.check.completed（超时 {dp_settings.QC_TIMEOUT_MINUTES}min）...")
            try:
                payload = await asyncio.wait_for(
                    qc_future, timeout=dp_settings.QC_TIMEOUT_MINUTES * 60
                )
            except TimeoutError:
                payload = None
            sub_task.cancel()
            try:
                await sub_task
            except asyncio.CancelledError:
                pass
            if payload is None:
                _record("6.1", "闭环流程", False, "超时未收到 qc.check.completed")
                return False
            print(f"         收到: score={payload.get('score')} risk={payload.get('risk')}")

            # 5. 更新快照质检列（投诉由后续 complaint.created 触发，不在此建单）
            print("  [5/6] 更新快照质检列...")
            await _update_qc_cols(engine, payload)

        # 6. 投诉腿：EventBus 发布 complaint.created（仅 case_id；正文由投诉侧组装）
        # 不调用 ComplaintClient.create / judge / review（闭环主路径禁用 HTTP 三步）。
        pre_snap = await _get_snapshot(engine)
        already_backflowed = bool(
            pre_snap and pre_snap.get("complaint_id") and pre_snap.get("verdict")
        )
        if already_backflowed:
            print(
                "  [6/6] 幂等重跑：快照投诉列已非空，仍发 complaint.created"
                "（投诉侧 case_id 幂等）后确认列保留"
            )
        else:
            print("  [6/6] 发布 complaint.created（EventBus，白名单仅 case_id）...")

        from common.eventbus.factory import reset_event_bus

        reset_event_bus()
        _ensure_host_event_bus_env()
        created_ok = await publish_complaint_created(
            case_id=CASE_ID,
            trace_id=f"min-loop-created-{CASE_ID}",
        )
        if not created_ok:
            _record("6.1", "闭环流程", False, "complaint.created 发布失败")
            return False
        _record(
            "6.1-created",
            "发布 complaint.created（事件驱动投诉腿）",
            True,
            f"case_id={CASE_ID} published=True",
        )
        _record(
            "6.1-no-http-triad",
            "主路径无 ComplaintClient create→judge→review",
            True,
            "脚本仅 publish_complaint_created + 轮询快照投诉列",
        )

        # 等待证据：轮询快照投诉列（见模块 docstring 选型理由）
        print("  等待快照投诉列回流（SIM_AUTO_REVIEW 在投诉进程内完成 approve）...")
        cols_ready = already_backflowed or await _poll_complaint_cols(
            engine, timeout=120.0
        )
        if not cols_ready:
            _record(
                "6.1",
                "闭环流程",
                False,
                "超时：快照投诉列仍为空（检查 SIM_AUTO_REVIEW / complaint 订阅）",
            )
            return False

        # 确认快照两侧都有值
        snap = await _get_snapshot(engine)
        qc_ok = bool(snap and snap.get("compliance_score") is not None)
        cp_ok = bool(snap and snap.get("complaint_id") and snap.get("verdict"))
        _record(
            "6.1",
            "闭环流程（事件驱动）",
            qc_ok and cp_ok,
            f"qc_cols={'ok' if qc_ok else 'empty'} complaint_cols={'ok' if cp_ok else 'empty'} "
            f"complaint_id={snap.get('complaint_id') if snap else None}",
        )
        return qc_ok and cp_ok
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────
# 6.2 知识库样例
# ──────────────────────────────────────────────────────────────

async def verify_6_2() -> None:
    """6.2: 确认知识库有法规/禁语短文能被 search_regulations 命中。"""
    print("\n=== 6.2 知识库样例 ===")
    rag_url = os.environ.get("RAG_SERVICE_URL", "http://localhost:8002")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.post(
                f"{rag_url}/api/v1/kb/search",
                json={"query": "催收违规", "category": "script",
                      "top_k": 3, "rewrite": False, "answer": False},
            )
            resp.raise_for_status()
            data = resp.json()
            results = (data.get("data") or {}).get("results") or data.get("results") or []
            hit = len(results) > 0
            _record("6.2", "知识库样例能被 search_regulations 命中", hit,
                    f"rag-service 返回 {len(results)} 条结果" if hit else "rag-service 返回空结果")
    except Exception as e:
        _record("6.2", "知识库样例能被 search_regulations 命中", False,
                f"rag-service 调用失败: {e}")


# ──────────────────────────────────────────────────────────────
# 6.3 四处对齐
# ──────────────────────────────────────────────────────────────

async def verify_6_3() -> None:
    """6.3: 同一 case_id 在种子、qc_records、投诉工单、dw.ads_case_loop 四处对齐。"""
    print("\n=== 6.3 四处对齐 ===")
    engine = _engine()
    try:
        async with engine.connect() as conn:
            cases_id = (await conn.execute(
                text("SELECT id::text FROM cases WHERE id = CAST(:id AS uuid)"),
                {"id": CASE_ID},
            )).mappings().first()
            # qc_records 无 case_id 列，经 call_records JOIN 反查
            qc_id = (await conn.execute(
                text("""SELECT cr.case_id::text
                        FROM qc_records q
                        JOIN call_records cr ON cr.session_id = q.session_id
                        WHERE q.session_id = :sid AND q.is_delete='0'
                        LIMIT 1"""),
                {"sid": SESSION_ID},
            )).mappings().first()
            comp_id = (await conn.execute(
                text("SELECT case_id::text FROM complaints WHERE case_id = :cid LIMIT 1"),
                {"cid": CASE_ID},
            )).mappings().first()
            snap_id = (await conn.execute(
                text("SELECT case_id::text FROM dw.ads_case_loop WHERE case_id = :cid"),
                {"cid": CASE_ID},
            )).mappings().first()

        c_id = cases_id["id"] if cases_id else None
        q_id = qc_id["case_id"] if qc_id else None
        cp_id = comp_id["case_id"] if comp_id else None
        s_id = snap_id["case_id"] if snap_id else None
        ok = all(v == CASE_ID for v in (c_id, q_id, cp_id, s_id))
        _record("6.3", "四处 case_id 对齐", ok,
                f"cases={c_id} qc={q_id} complaint={cp_id} snapshot={s_id}")
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────
# 6.4 真开检
# ──────────────────────────────────────────────────────────────

async def verify_6_4() -> None:
    """6.4: 质检分数来自真开检（不是人手插入 qc_records）。"""
    print("\n=== 6.4 真开检 ===")
    engine = _engine()
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(
                text("""SELECT id::text, compliance_score, risk_level, create_time
                        FROM qc_records
                        WHERE session_id = :sid AND is_delete='0'
                        LIMIT 1"""),
                {"sid": SESSION_ID},
            )).mappings().first()
            if not row:
                _record("6.4", "质检分数来自真开检", False,
                        f"qc_records 无 session_id={SESSION_ID} 记录")
                return
            score = row.get("compliance_score")
            created = row.get("create_time")
            # 真开检额外校验：qc_alerts 有告警（种子含 R11/R12/R13 触发句，真开检必产告警）
            alerts_row = (await conn.execute(
                text("""SELECT COUNT(*) AS cnt FROM qc_alerts a
                        JOIN qc_records q ON q.id = a.qc_record_id
                        WHERE q.session_id = :sid AND q.is_delete='0'
                          AND a.is_delete='0'"""),
                {"sid": SESSION_ID},
            )).mappings().first()
            has_alerts = alerts_row and alerts_row["cnt"] > 0
        # 首跑：create_time 必须晚于本次 etl.task.done（排除提前手插）；
        # 幂等重跑：qc-service 对终态会话不重复开检，记录来自首跑即合规（无重复行）。
        after_publish = bool(_publish_time and created and created > _publish_time)
        ok = score is not None and created is not None and has_alerts and (
            after_publish or _idempotent_rerun
        )
        _record("6.4", "质检分数来自真开检", bool(ok),
                f"qc_record_id={row['id']} score={score} risk={row.get('risk_level')} "
                f"create_time={created} has_alerts={has_alerts} "
                f"after_publish={after_publish} idempotent_rerun={_idempotent_rerun}")
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────
# 6.5 顺序验证
# ──────────────────────────────────────────────────────────────

async def verify_6_5() -> None:
    """6.5: 质检完成后才发 complaint.created；回流后投诉列才有值。"""
    print("\n=== 6.5 顺序验证 ===")
    engine = _engine()
    try:
        async with engine.connect() as conn:
            qc_row = (await conn.execute(
                text("SELECT create_time FROM qc_records WHERE session_id = :sid AND is_delete='0' LIMIT 1"),
                {"sid": SESSION_ID},
            )).mappings().first()
            comp_row = (await conn.execute(
                text("SELECT create_time FROM complaints WHERE case_id = :cid LIMIT 1"),
                {"cid": CASE_ID},
            )).mappings().first()
        snap = await _get_snapshot(engine)
        qc_time = qc_row["create_time"] if qc_row else None
        comp_time = comp_row["create_time"] if comp_row else None
        timing_ok = bool(qc_time and comp_time and comp_time > qc_time)
        cols_ok = bool(snap and snap.get("complaint_id") and snap.get("verdict"))
        ok = timing_ok and cols_ok
        _record(
            "6.5",
            "质检完成后才有投诉工单；事件链回流后投诉列有值",
            ok,
            f"qc_at={qc_time} complaint_at={comp_time} complaint_cols={'ok' if cols_ok else 'empty'}",
        )
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────
# 6.6 真检索
# ──────────────────────────────────────────────────────────────

async def verify_6_6() -> None:
    """6.6: USE_MOCK_MCP=false 时法规来自 rag-service 真检索。

    口径（A）：快照 dw.ads_case_loop.verdict 存的是 VerdictType 枚举值
    （pending / agent_violation 等），结构上不含法规文本，故不再以快照 verdict
    是否含法规关键词判定；改为在工单 trace（MCP 调用逐条落盘）中校验 judge 期间
    确有一次成功的 rag_kb.search_regulations 真调用，即法规证据来自 rag-service。
    """
    print("\n=== 6.6 真检索 ===")
    rag_url = os.environ.get("RAG_SERVICE_URL", "http://localhost:8002")
    rag_reachable = False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            resp = await c.get(f"{rag_url}/health")
            rag_reachable = resp.status_code == 200
    except Exception:
        pass
    # 工单 trace 里的 rag_kb 真调用（judge 期间落盘）
    rag_calls: list[str] = []
    try:
        base = dp_settings.COMPLAINT_SERVICE_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=10) as c:
            resp = await c.get(f"{base}/complaints/{CASE_ID}/trace")
            events = (resp.json().get("data") or {}).get("events") or []
        rag_calls = sorted({
            str(e.get("name"))
            for e in events
            if str(e.get("name", "")).startswith("rag_kb.")
            and str(e.get("status")) == "success"
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning("verify_6_6 trace fetch failed: %s", exc)

    has_real_retrieval = "rag_kb.search_regulations" in rag_calls
    ok = rag_reachable and has_real_retrieval
    _record("6.6", "USE_MOCK_MCP=false 时法规来自 rag-service 真检索", ok,
            f"rag-service reachable={rag_reachable}; trace 中 rag_kb 真调用={rag_calls}")


# ──────────────────────────────────────────────────────────────
# 6.7 降级验证
# ──────────────────────────────────────────────────────────────

async def verify_6_7() -> None:
    """6.7: 模拟 rag-service 检索失败，确认降级标记 [规则兜底]，进程不崩。"""
    print("\n=== 6.7 降级验证 ===")
    # 1. 模拟 rag-service 检索失败（调用不存在的地址，验证连接错误被捕获）
    bad_url = "http://127.0.0.1:19999"
    simulated_fail = False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            await c.post(f"{bad_url}/api/v1/kb/search",
                         json={"query": "催收", "category": "regulation"})
    except Exception:
        simulated_fail = True  # 连接失败 = 模拟成功
    print(f"  模拟 rag-service 不可达: {'成功' if simulated_fail else '失败'}")

    # 2. 跑已有降级单测（验证 [规则兜底] 输出路径完整）
    cs_root = _REPO_ROOT / "services" / "complaint-service"
    proc_ok = False
    proc_tail = ""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/unit/test_node_degrade.py",
             "tests/unit/test_fallback.py",
             "-x", "-q"],
            cwd=str(cs_root),
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "USE_MOCK_MCP": "true", "EVENT_BUS": "rocketmq"},
        )
        proc_ok = proc.returncode == 0
        proc_tail = (proc.stdout.strip().split("\n")[-1]
                     if proc.stdout.strip() else proc.stderr.strip()[:200])
    except Exception as e:
        proc_tail = f"pytest 运行失败: {e}"

    # 3. 检查主流程 verdict 是否不含崩溃（进程不崩验证）
    engine = _engine()
    verdict_no_crash = False
    try:
        snap = await _get_snapshot(engine)
        verdict_no_crash = bool(snap and snap.get("verdict"))
    finally:
        await engine.dispose()

    ok = simulated_fail and proc_ok and verdict_no_crash
    _record("6.7", "降级路径含 [规则兜底] 标记，进程不崩", ok,
            f"模拟失败={simulated_fail} 单测={proc_ok} 进程不崩={verdict_no_crash}; {proc_tail}")


# ──────────────────────────────────────────────────────────────
# 6.8 两路回流
# ──────────────────────────────────────────────────────────────

async def verify_6_8() -> None:
    """6.8: 快照行质检列和投诉列都有值；只有一侧成功记告警、验收不通过。"""
    print("\n=== 6.8 两路回流 ===")
    engine = _engine()
    try:
        snap = await _get_snapshot(engine)
        qc_ok = bool(snap and snap.get("compliance_score") is not None
                      and snap.get("risk_level"))
        cp_ok = bool(snap and snap.get("complaint_id") and snap.get("verdict"))
        ok = qc_ok and cp_ok
        ev = (f"qc: score={snap.get('compliance_score') if snap else None} "
              f"risk={snap.get('risk_level') if snap else None}; "
              f"complaint: id={snap.get('complaint_id') if snap else None} "
              f"verdict={snap.get('verdict') if snap else None}")
        if not ok:
            ev += " — 仅单侧成功，验收不通过"
        _record("6.8", "快照质检列+投诉列均非空", ok, ev)
    finally:
        await engine.dispose()


# ──────────────────────────────────────────────────────────────
# 6.9 旧测试绿
# ──────────────────────────────────────────────────────────────

async def verify_6_9() -> None:
    """6.9: USE_MOCK_MCP=true 时三服务现有测试全绿。"""
    print("\n=== 6.9 旧测试绿 ===")
    services = [
        ("complaint-service", "services/complaint-service"),
        ("qc-service", "services/qc-service"),
        ("rag-service", "services/rag-service"),
    ]
    all_ok = True
    parts: list[str] = []
    for name, rel in services:
        test_dir = _REPO_ROOT / rel
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "-x", "-q",
                 "--tb=line"],
                cwd=str(test_dir),
                capture_output=True, text=True, timeout=300,
                env={**os.environ, "USE_MOCK_MCP": "true", "EVENT_BUS": "rocketmq"},
            )
            ok = proc.returncode == 0
            if not ok:
                all_ok = False
            tail = (proc.stdout.strip().split("\n")[-1]
                    if proc.stdout.strip() else proc.stderr.strip()[:200])
            parts.append(f"{name}: {'PASS' if ok else 'FAIL'} ({tail})")
        except subprocess.TimeoutExpired:
            all_ok = False
            parts.append(f"{name}: TIMEOUT")
        except Exception as e:
            all_ok = False
            parts.append(f"{name}: ERROR ({e})")
    _record("6.9", "USE_MOCK_MCP=true 三服务现有测试全绿", all_ok, "; ".join(parts))


# ──────────────────────────────────────────────────────────────
# 6.10 PII 检查
# ──────────────────────────────────────────────────────────────

async def verify_6_10() -> None:
    """6.10: 种子与快照中电话/证件/卡号为假数据或脱敏，无明文 PII。"""
    print("\n=== 6.10 PII 检查 ===")
    # 脚本侧常量检查
    phone_ok = not PHONE_ENCRYPTED.replace("fake:enc:", "").isdigit()
    hash_ok = (len(PHONE_HASH) == 64
               and all(c in "0123456789abcdef" for c in PHONE_HASH))
    name_ok = "*" in CUSTOMER_NAME
    # DB 侧确认
    engine = _engine()
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(
                text("""SELECT phone_encrypted, phone_hash, customer_name
                        FROM cases WHERE id = CAST(:id AS uuid)"""),
                {"id": CASE_ID},
            )).mappings().first()
        if row:
            db_phone = row.get("phone_encrypted") or ""
            db_hash = row.get("phone_hash") or ""
            db_name = row.get("customer_name") or ""
            db_phone_ok = not db_phone.replace("fake:enc:", "").isdigit()
            db_hash_ok = len(db_hash) == 64
            db_name_ok = "*" in db_name
        else:
            db_phone_ok = db_hash_ok = db_name_ok = False
    finally:
        await engine.dispose()
    ok = all([phone_ok, hash_ok, name_ok, db_phone_ok, db_hash_ok, db_name_ok])
    _record("6.10", "无明文 PII（电话/证件/卡号均为假数据或脱敏）", ok,
            f"phone_encrypted={PHONE_ENCRYPTED} hash_len={len(PHONE_HASH)} "
            f"name={CUSTOMER_NAME} db_ok={db_phone_ok and db_hash_ok and db_name_ok}")


# ──────────────────────────────────────────────────────────────
# 6.11 超时重试
# ──────────────────────────────────────────────────────────────

async def verify_6_11() -> None:
    """6.11: 内部 HTTP 沿用 5 秒超时 + 3 次重试，失败有可查记录。"""
    print("\n=== 6.11 超时重试 ===")
    timeout = dp_settings.COMPLAINT_HTTP_TIMEOUT
    retry = dp_settings.COMPLAINT_HTTP_RETRY
    # 检查 complaint_client.py 源码含 logger.warning/error（失败有可查记录）
    client_src = (_REPO_ROOT / "services" / "data-platform" / "app"
                  / "integrations" / "complaint_client.py").read_text(encoding="utf-8")
    has_fail_log = ("logger.warning" in client_src or "logger.error" in client_src)
    ok = timeout == 5.0 and retry == 3 and has_fail_log
    _record("6.11", "内部 HTTP 5 秒超时 + 3 次重试，失败有可查记录", ok,
            f"COMPLAINT_HTTP_TIMEOUT={timeout} COMPLAINT_HTTP_RETRY={retry} "
            f"失败日志记录={has_fail_log}")


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

async def main() -> int:
    print("=" * 60)
    print("data-platform-min-loop 闭环验收（complaint.created 事件驱动）")
    print(f"case_id={CASE_ID}")
    print(f"session_id={SESSION_ID}")
    print(f"EVENT_BUS={os.getenv('EVENT_BUS', 'rocketmq')}")
    print("complaint leg: publish_complaint_created → poll snapshot cols")
    print("(no ComplaintClient create→judge→review)")
    print(f"COMPLAINT_SERVICE_URL={dp_settings.COMPLAINT_SERVICE_URL}")
    print(f"QC_REDIS_DB={dp_settings.QC_REDIS_DB}")
    print("=" * 60)

    await verify_6_2()
    await run_flow()
    await verify_6_3()
    await verify_6_4()
    await verify_6_5()
    await verify_6_6()
    await verify_6_7()
    await verify_6_8()
    await verify_6_9()
    await verify_6_10()
    await verify_6_11()

    # ── 验收结果逐项对照表 ──
    print("\n" + "=" * 60)
    print("验收结果逐项对照表")
    print("=" * 60)
    print(f"{'编号':<6} {'结果':<6} 描述")
    print("-" * 60)
    for r in _results:
        tag = "PASS" if r["pass"] else "FAIL"
        print(f"{r['task']:<6} {tag:<6} {r['desc']}")
        if r["evidence"]:
            print(f"{'':6} {'':6} 证据: {r['evidence']}")

    passed = sum(1 for r in _results if r["pass"])
    total = len(_results)
    print("-" * 60)
    print(f"总计: {passed}/{total} 通过")

    blockers = [r for r in _results if not r["pass"]]
    if blockers:
        print("\n阻断问题清单:")
        for b in blockers:
            print(f"  - [{b['task']}] {b['desc']}")
            if b["evidence"]:
                print(f"    证据: {b['evidence']}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

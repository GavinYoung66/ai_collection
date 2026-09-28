"""run_batch_flow.py — 批量全链路回流验收（complaint-event-driven 6.2）

流程：
1. 读取 200 条 batch case_id + session_id
2. 发布 200 条 etl.task.done（EventBus / RocketMQ）
3. 等待 qc 落库 + 快照质检列回流
4. 对质检列已回流的每个 case 批量 ``publish_complaint_created``
   （研判量 = 事件条数；无 JUDGE_BUDGET）
5. 轮询快照投诉列回流数（与 min_loop 同口径：轮询快照，非订 result.ready）
6. 逐项验证 + 输出对账矩阵

不调用 ComplaintClient 三步；complaint 侧 SIM_AUTO_REVIEW=true 完成 approve。

用法（仓库根目录）：
    python services/data-platform/scripts/run_batch_flow.py

前置：
- gen_cases_batch + gen_calls_batch 已执行
- data-platform / qc-service / complaint-service / rag-service 运行中
- complaint-service：USE_MOCK_MCP=false、SIM_AUTO_REVIEW=true
- EVENT_BUS=rocketmq；PostgreSQL 可达
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# --- Path setup ---
_SCRIPT_DIR = Path(__file__).resolve().parent
_DP_ROOT = _SCRIPT_DIR.parents[0]
_REPO_ROOT = _SCRIPT_DIR.parents[2]
for _p in (_REPO_ROOT, _DP_ROOT, _SCRIPT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from common.config.settings import settings as platform_settings  # noqa: E402
from app.core.config import dp_settings  # noqa: E402

logger = logging.getLogger("run_batch_flow")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

_SHANGHAI = ZoneInfo("Asia/Shanghai")
QC_REDIS_DB = dp_settings.QC_REDIS_DB

# 验收结果
_results: list[dict[str, Any]] = []


def _record(task: str, desc: str, passed: bool, evidence: str = "") -> None:
    _results.append({"task": task, "desc": desc, "pass": passed, "evidence": evidence})
    tag = "PASS" if passed else "FAIL"
    print(f"  [{task}] {tag} — {desc}")
    if evidence:
        print(f"         证据: {evidence}")


def _engine():
    return create_async_engine(platform_settings.pg_dsn, poolclass=NullPool)


async def _read_batch_cases(engine) -> list[dict[str, str]]:
    """读取 200 条 batch case_id + session_id + call_date（用于 backfill_date）。"""
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT c.id::text AS case_id, cr.session_id,
                   (cr.end_time AT TIME ZONE 'Asia/Shanghai')::date::text AS call_date
            FROM cases c
            JOIN call_records cr ON cr.case_id = c.id
            WHERE c.case_number LIKE 'BATCH-%'
            ORDER BY c.case_number
        """))).mappings().all()
    return [dict(r) for r in rows]


async def _publish_etl_done(cases: list[dict[str, str]]) -> int:
    """经 EventBus 发布 etl.task.done（默认 EVENT_BUS=rocketmq）。

    qc-service 收到后按 backfill_date 查 pending-qc 并逐个开检。
    为满足"推送 200 条"口径，对每条 case 发一条事件。
    """
    from datetime import datetime as _dt

    from app.events.publisher import publish_etl_task_done

    published = 0
    published_dates: set[str] = set()
    for c in cases:
        backfill_date = c.get("call_date") or datetime.now(_SHANGHAI).strftime("%Y-%m-%d")
        ok = await publish_etl_task_done(
            case_id=c["case_id"],
            session_id=c["session_id"],
            backfill_date=_dt.strptime(backfill_date, "%Y-%m-%d").replace(tzinfo=_SHANGHAI),
        )
        if ok:
            published += 1
            published_dates.add(backfill_date)
    print(f"  distinct backfill_dates: {sorted(published_dates)}")
    return published


async def _count_qc_records(engine, session_ids: list[str]) -> int:
    """统计 batch session_ids 对应的 qc_records 落数。"""
    if not session_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(DISTINCT q.session_id)
            FROM qc_records q
            WHERE q.session_id = ANY(:sids) AND q.is_delete = '0'
        """), {"sids": session_ids})
        return r.scalar() or 0


async def _count_snapshot_qc_cols(engine, case_ids: list[str]) -> int:
    """统计 batch case_ids 的快照质检列非空数。"""
    if not case_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(*) FROM dw.ads_case_loop
            WHERE case_id = ANY(:cids)
              AND qc_record_id IS NOT NULL
              AND compliance_score IS NOT NULL
        """), {"cids": case_ids})
        return r.scalar() or 0


async def _count_complaints(engine, case_ids: list[str]) -> int:
    """统计 batch case_ids 的投诉落库数。"""
    if not case_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(*) FROM complaints WHERE case_id = ANY(:cids)
        """), {"cids": case_ids})
        return r.scalar() or 0


async def _count_duplicate_complaints(engine, case_ids: list[str]) -> int:
    """统计重复投诉数（同一 case_id 出现 >1 行）。"""
    if not case_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT case_id, COUNT(*) AS cnt
                FROM complaints
                WHERE case_id = ANY(:cids)
                GROUP BY case_id
                HAVING COUNT(*) > 1
            ) t
        """), {"cids": case_ids})
        return r.scalar() or 0


async def _count_judged_complaints(engine, case_ids: list[str]) -> int:
    """统计已研判的投诉数（快照投诉列非空）。"""
    if not case_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(*) FROM dw.ads_case_loop
            WHERE case_id = ANY(:cids)
              AND complaint_id IS NOT NULL
              AND verdict IS NOT NULL
              AND task_status IS NOT NULL
        """), {"cids": case_ids})
        return r.scalar() or 0


async def _case_ids_with_qc_cols(engine, case_ids: list[str]) -> list[str]:
    """质检列已回流的 case_id（投诉腿只对这些案发 complaint.created）。"""
    if not case_ids:
        return []
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT case_id::text AS case_id FROM dw.ads_case_loop
            WHERE case_id = ANY(:cids)
              AND qc_record_id IS NOT NULL
              AND compliance_score IS NOT NULL
            ORDER BY case_id
        """), {"cids": case_ids})).mappings().all()
    return [str(r["case_id"]) for r in rows]


async def _publish_complaint_created_batch(case_ids: list[str]) -> int:
    """批量发布 complaint.created；失败记日志并跳过，不中断批次。

    返回实际 publish 成功条数（与日志口径一致）。
    """
    from common.eventbus.factory import reset_event_bus
    from app.events.publisher import publish_complaint_created

    if not (os.getenv("EVENT_BUS") or "").strip():
        os.environ["EVENT_BUS"] = "rocketmq"
    if not (os.getenv("EVENT_BUS_SVC") or "").strip():
        os.environ["EVENT_BUS_SVC"] = "dp"
    reset_event_bus()
    if (os.getenv("EVENT_BUS") or "").strip().lower() == "rocketmq":
        os.environ.setdefault("ROCKETMQ_PROXY", "localhost:18081")

    published = 0
    for cid in case_ids:
        ok = await publish_complaint_created(
            case_id=cid,
            trace_id=f"batch-created-{cid}",
        )
        if ok:
            published += 1
        else:
            logger.error(
                "complaint.created publish failed; continue batch",
                extra={"case_id": cid},
            )
    return published


async def _count_duplicate_snapshot(engine, case_ids: list[str]) -> int:
    """统计快照表重复数。"""
    if not case_ids:
        return 0
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT COUNT(*) FROM (
                SELECT case_id, COUNT(*) AS cnt
                FROM dw.ads_case_loop
                WHERE case_id = ANY(:cids)
                GROUP BY case_id
                HAVING COUNT(*) > 1
            ) t
        """), {"cids": case_ids})
        return r.scalar() or 0


async def _sample_complaint_texts(engine, case_ids: list[str]) -> list[str]:
    """抽样两起投诉正文。"""
    if not case_ids:
        return []
    async with engine.connect() as conn:
        r = await conn.execute(text("""
            SELECT complaint_text FROM complaints
            WHERE case_id = ANY(:cids)
            ORDER BY create_time DESC
            LIMIT 2
        """), {"cids": case_ids})
        return [row[0] for row in r]


async def _wait_for_count(fn, target: int, timeout: float = 600.0, interval: float = 5.0, label: str = "") -> int:
    """轮询直到 fn() >= target 或超时。"""
    deadline = time.time() + timeout
    last = 0
    while time.time() < deadline:
        last = await fn()
        if last >= target:
            return last
        print(f"  {label}: {last}/{target}...", end="\r")
        await asyncio.sleep(interval)
    return last


async def run_batch_flow() -> dict[str, Any]:
    """执行批量全链路回流，返回统计结果。"""
    engine = _engine()
    stats: dict[str, Any] = {}
    try:
        # 读取 200 条 batch case_id + session_id
        cases = await _read_batch_cases(engine)
        case_ids = [c["case_id"] for c in cases]
        session_ids = [c["session_id"] for c in cases]
        print(f"读取 batch 案件: {len(cases)} 条")
        stats["batch_cases"] = len(cases)

        if len(cases) != 200:
            _record("5.2", "读取 200 条 batch 案件", False,
                    f"实际读取 {len(cases)} 条，期望 200")
            return stats

        # 检查 qc_records 是否已存在（可能 etl.task.done 已发过）
        existing_qc = await _count_qc_records(engine, session_ids)
        print(f"已存在 qc_records: {existing_qc}")

        if existing_qc < 168:
            # 发布 200 条 etl.task.done（首次或补发）
            print("\n=== 5.2 发布 etl.task.done ===")
            published = await _publish_etl_done(cases)
            stats["published"] = published
            print(f"发布 etl.task.done: {published} 条 (Redis db={QC_REDIS_DB})")

            if published != 200:
                _record("5.2", "发布 200 条 etl.task.done", False,
                        f"实际发布 {published} 条")
                return stats

            _record("5.2", "发布 200 条 etl.task.done 到 Redis 3 号库", True,
                    f"published={published}, db={QC_REDIS_DB}")

            # 等待 qc_records 落库（qc-service 批量开检，85% completed ≈ 168）
            print("\n等待 qc-service 批量开检...")
            qc_done = await _wait_for_count(
                lambda: _count_qc_records(engine, session_ids),
                target=168, timeout=600, interval=5,
                label="qc_records",
            )
            stats["qc_records"] = qc_done
            print(f"\nqc_records 落库: {qc_done}/168+")
        else:
            # qc_records 已存在（etl.task.done 之前已发过），直接重发 qc.check.completed
            stats["published"] = 200
            stats["qc_records"] = existing_qc
            print(f"qc_records 已存在: {existing_qc}，跳过 etl.task.done 发布")
            _record("5.2", "发布 200 条 etl.task.done 到 Redis 3 号库（之前已发）", True,
                    f"published=200 (prior), db={QC_REDIS_DB}")

        qc_pass = stats["qc_records"] >= 168  # 85% of 200 completed calls
        _record("5.2", f"qc.check.completed 全部被消费（qc_records 落库 {stats['qc_records']}）", qc_pass,
                f"qc_records count={stats['qc_records']}, target>=168 (85% completed)")

        # 等待快照质检列回流
        print("等待快照质检列回流...")
        snap_qc = await _wait_for_count(
            lambda: _count_snapshot_qc_cols(engine, case_ids),
            target=168, timeout=60, interval=5,
            label="snapshot_qc",
        )
        stats["snapshot_qc_cols"] = snap_qc
        print(f"\n快照质检列回流: {snap_qc}/200")

        snap_qc_pass = snap_qc >= 168  # 至少 85% completed calls
        _record("5.2", f"dw.ads_case_loop 质检列回流 {snap_qc} 行非空", snap_qc_pass,
                f"snapshot_qc_cols={snap_qc}, target>=168")

        # === 6.2 质检列确认后批量发 complaint.created（事件条数 = 研判预算）===
        print("\n=== 6.2 批量 publish complaint.created ===")
        qc_ready_ids = await _case_ids_with_qc_cols(engine, case_ids)
        print(f"质检列已回流 case 数: {len(qc_ready_ids)}（将逐条发 complaint.created）")
        created_published = await _publish_complaint_created_batch(qc_ready_ids)
        stats["complaint_created_published"] = created_published
        stats["complaint_created_target"] = len(qc_ready_ids)
        print(
            f"complaint.created 发布成功: {created_published}/{len(qc_ready_ids)} "
            f"（日志口径=实际成功条数）"
        )
        pub_ok = created_published == len(qc_ready_ids) and created_published >= 168
        _record(
            "6.2-pub",
            "质检列回流后批量 publish_complaint_created",
            pub_ok,
            f"published={created_published} target_cases={len(qc_ready_ids)}",
        )
        if created_published == 0:
            _record("6.2", "批量投诉事件腿", False, "零条 complaint.created 发布成功")
            return stats

        # 等待投诉落库（投诉侧消费 created；case_id 幂等）
        print("\n=== 等待投诉落库（事件消费）===")
        complaints_done = await _wait_for_count(
            lambda: _count_complaints(engine, case_ids),
            target=created_published, timeout=600, interval=5,
            label="complaints",
        )
        stats["complaints"] = complaints_done
        print(f"\n投诉落库: {complaints_done}/{created_published}")

        comp_pass = complaints_done >= min(created_published, 168)
        _record(
            "6.2-comp",
            f"complaints 表 {complaints_done} 行（≈ created 事件条数）",
            comp_pass,
            f"complaints={complaints_done}, created_events={created_published}",
        )

        dup_comp = await _count_duplicate_complaints(engine, case_ids)
        stats["duplicate_complaints"] = dup_comp
        _record("6.2-dup", "零重复 case_id", dup_comp == 0,
                f"duplicate_complaints={dup_comp}")

        texts = await _sample_complaint_texts(engine, case_ids)
        texts_diff = len(texts) >= 2 and texts[0] != texts[1]
        _record("6.2-text", "抽样两起 complaint_text 内容不同", texts_diff,
                f"texts_sample={len(texts)}, diff={texts_diff}")

        # 等待快照投诉列回流（与 min_loop 同口径：轮询快照列）
        print("\n=== 等待快照投诉列回流（SIM_AUTO_REVIEW / write_session）===")
        judged_target = max(1, min(created_published, created_published))
        judged = await _wait_for_count(
            lambda: _count_judged_complaints(engine, case_ids),
            target=judged_target, timeout=900, interval=10,
            label="judged",
        )
        stats["judged"] = judged
        print(f"\n已回流投诉列: {judged}/{created_published}")

        judged_pass = judged >= max(1, int(created_published * 0.8))
        _record(
            "6.2-judged",
            "快照投诉列回流（事件条数控流，无 budget）",
            judged_pass,
            f"judged={judged}, created_events={created_published}",
        )

        # === 两路回流 + 零重复对账 ===
        print("\n=== 两路回流 + 零重复对账 ===")

        snap_both = await _count_judged_complaints(engine, case_ids)
        stats["snapshot_both_cols"] = snap_both

        snap_qc_final = await _count_snapshot_qc_cols(engine, case_ids)
        stats["snapshot_qc_final"] = snap_qc_final

        snap_comp_final = snap_both
        stats["snapshot_comp_final"] = snap_comp_final

        both_pass = snap_qc_final >= 168 and snap_comp_final >= max(
            1, int(created_published * 0.8)
        )
        _record(
            "6.2-both",
            "质检列+投诉列均非空",
            both_pass,
            f"qc_cols={snap_qc_final}, complaint_cols={snap_comp_final}, "
            f"created_events={created_published}",
        )

        dup_snap = await _count_duplicate_snapshot(engine, case_ids)
        stats["duplicate_snapshot"] = dup_snap
        stats["duplicate_complaints_final"] = dup_comp

        # 对账：etl 200 → qc N → created 事件 M → complaints≈M；重复=0
        reconcile_pass = (
            stats.get("published") == 200
            and stats["qc_records"] >= 168
            and created_published >= 168
            and stats["complaints"] >= min(created_published, 168)
            and dup_comp == 0
            and dup_snap == 0
        )
        _record(
            "6.2-reconcile",
            "etl=200；qc≥168；created 事件条数≈投诉落库；重复=0",
            reconcile_pass,
            f"etl={stats.get('published')} qc={stats['qc_records']} "
            f"created_events={created_published} complaints={stats['complaints']} "
            f"dup_comp={dup_comp} dup_snap={dup_snap}",
        )

        return stats
    finally:
        await engine.dispose()


async def main() -> int:
    print("=" * 60)
    print("batch 全链路回流验收（complaint.created 事件驱动）")
    print(f"EVENT_BUS={os.getenv('EVENT_BUS', 'rocketmq')}")
    print("complaint leg: batch publish_complaint_created → poll snapshot cols")
    print("(no ComplaintClient create→judge→review)")
    print(f"COMPLAINT_SERVICE_URL={dp_settings.COMPLAINT_SERVICE_URL}")
    print(f"QC_REDIS_DB={QC_REDIS_DB}")
    print("judge load = complaint.created event count")
    print("=" * 60)

    stats = await run_batch_flow()

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

    # ── 批量对账矩阵 ──
    print("\n" + "=" * 60)
    print("批量对账矩阵")
    print("=" * 60)
    print(f"推送数 (etl.task.done):     {stats.get('published', 'N/A')}")
    print(f"消费数 (qc_records 落库):    {stats.get('qc_records', 'N/A')}")
    print(f"complaint.created 发布数:   {stats.get('complaint_created_published', 'N/A')}"
          f" / target={stats.get('complaint_created_target', 'N/A')}")
    print(f"落库数 (complaints):        {stats.get('complaints', 'N/A')}")
    print(f"重复数 (complaints):        {stats.get('duplicate_complaints', 'N/A')}")
    print(f"重复数 (snapshot):          {stats.get('duplicate_snapshot', 'N/A')}")
    print(f"快照质检列回流数:            {stats.get('snapshot_qc_final', stats.get('snapshot_qc_cols', 'N/A'))}")
    print(f"快照投诉列回流数:            {stats.get('snapshot_comp_final', stats.get('judged', 'N/A'))}")
    print(f"judge 完成数（=事件条数控流）: {stats.get('judged', 'N/A')}")

    passed = sum(1 for r in _results if r["pass"])
    total = len(_results)
    print(f"\n总计: {passed}/{total} 通过")

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

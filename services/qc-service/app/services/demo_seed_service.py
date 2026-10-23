"""演示灌数与 pending 列表。待检不插 qc_records；call_id = call_records.id。"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from app.events.publisher import event_call_id
from app.repositories.demo_repo import DemoSeedRepo
from app.repositories.demo_store import DemoSessionStore, DemoTraceStore
from app.repositories.quality_repo import QualityRepo
from app.seeds.demo_scenarios import DEMO_SCENARIOS
from app.services.demo_trace import make_trace_event, safe_append, safe_set_meta
from common.security.rbac import create_token

_VALID_QC = frozenset({"DONE", "PENDING_REVIEW", "REVIEWED"})


class DemoSeedService:
    def __init__(
        self,
        repo: DemoSeedRepo,
        store: DemoSessionStore,
        quality_repo: QualityRepo,
    ) -> None:
        self.repo = repo
        self.store = store
        self.quality_repo = quality_repo

    async def seed(self) -> dict[str, Any]:
        reviewer_id = await self.repo.ensure_reviewer()
        now = datetime.now(timezone.utc)
        duration = 120
        start_time = now - timedelta(seconds=duration)
        sessions: list[dict[str, Any]] = []
        for scenario in DEMO_SCENARIOS:
            case_id = uuid4()
            call_id = uuid4()
            session_id = f"qc_demo_{uuid4().hex}"
            digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
            await self.repo.insert_ended_call(
                case_id=case_id,
                call_id=call_id,
                session_id=session_id,
                case_number=f"QC-DEMO-{uuid4().hex[:12]}",
                customer_name=scenario["customer_name"],
                phone_encrypted=f"enc_{digest[:24]}",
                phone_hash=digest,
                overdue_amount=scenario["overdue_amount"],
                overdue_days=scenario["overdue_days"],
                start_time=start_time,
                end_time=now,
                duration_seconds=duration,
                turns=scenario["turns"],
            )
            sessions.append(
                {
                    "session_id": session_id,
                    "scenario": scenario["code"],
                    "label": scenario["label"],
                    "case_id": str(case_id),
                    "call_id": str(call_id),
                }
            )
        await self.store.add_many(sessions)
        trace_store = DemoTraceStore(self.store._redis)
        for item in sessions:
            await safe_append(
                trace_store,
                item["session_id"],
                make_trace_event(
                    category="seed",
                    name="demo_seed",
                    status="ok",
                    summary=f"scenario={item['scenario']}",
                ),
            )
            await safe_set_meta(
                trace_store,
                item["session_id"],
                call_id=event_call_id(item["call_id"], session_id=item["session_id"]),
            )
        commit = getattr(self.repo.session, "commit", None)
        if commit is not None:
            await commit()
        token = create_token({"sub": str(reviewer_id), "role": "qc_reviewer"})
        return {
            "session_ids": [item["session_id"] for item in sessions],
            "sessions": sessions,
            "token": token,
            "reviewer_id": str(reviewer_id),
        }

    async def pending(self) -> dict[str, Any]:
        registered = await self.store.list_all()
        items: list[dict[str, Any]] = []
        for item in registered:
            session_id = str(item.get("session_id") or "")
            if not session_id:
                continue
            row = await self.quality_repo.get_by_session_id(session_id)
            if row is not None and row.qc_status in _VALID_QC:
                continue
            items.append(
                {
                    "session_id": session_id,
                    "scenario": item.get("scenario"),
                    "label": item.get("label"),
                    "case_id": item.get("case_id"),
                    "call_id": item.get("call_id"),
                }
            )
        return {"items": items, "total": len(items)}

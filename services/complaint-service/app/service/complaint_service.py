"""投诉工单服务门面（批次8 收敛）。

批次7 内存 stub 收敛为薄门面：真相源为 session_service，
路由层接口签名保持不变；cache 仅存 verdict 等业务字段，task_status 始终回写自 session。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.events.publisher import publisher
from app.models.state import ReviewAction, TaskStatus, VerdictType
from app.service.security import prepare_agent_log
from app.service.session_service import session_service


class ComplaintNotFound(Exception):
    pass


class ComplaintService:
    """门面：委托 session_service，保留路由层所需字段。"""

    def __init__(self) -> None:
        # 业务字段缓存（verdict 等）；task_status 以 session 为准
        self._cache: dict[str, dict] = {}

    def _sync_from_session(self, rec: dict) -> dict:
        sid = rec.get("session_id")
        if not sid:
            return rec
        session = session_service.get_session(sid)
        if session is None:
            return rec
        rec["task_status"] = session["task_status"]
        rec["updated_at"] = session["updated_at"]
        return rec

    def _session_to_record(self, session: dict) -> dict:
        cid = session["complaint_id"]
        if cid not in self._cache:
            self._cache[cid] = {
                "complaint_id": cid,
                "complaint_text": session["complaint_text"],
                "session_id": session["session_id"],
                "case_id": None,
                "channel": "phone",
                "task_status": session["task_status"],
                "verdict": None,
                "confidence": None,
                "response_draft": None,
                "regulation_refs": [],
                "rag_source_ids": [],
                "allowed_rag_doc_ids": [],
                "stop_call_flag": False,
                "agent_logs": [],
                "trace_events": [],
                "langsmith_run_id": None,
                "review_action": None,
                "created_at": session["created_at"],
                "updated_at": session["updated_at"],
            }
        return self._sync_from_session(self._cache[cid])

    def create(self, complaint_text: str, session_id: Optional[str] = None,
               case_id: Optional[str] = None, channel: str = "phone") -> dict:
        cid = case_id or str(uuid.uuid4())
        sid = session_service.create_session(cid, complaint_text, session_id=session_id)
        session = session_service.get_session(sid)
        assert session is not None
        rec = self._session_to_record(session)
        rec["case_id"] = case_id
        rec["channel"] = channel
        return rec

    def seed_demo(self) -> list[dict]:
        """Create V2-scenario complaints（含 pending 样例，供 V4 验收⑤）。"""
        from tests.mock.data.seed_complaints_v2 import (
            SCENARIO_PENDING,
            iter_demo_seed_creates,
        )

        created: list[dict] = []
        for payload in iter_demo_seed_creates():
            rec = self.create(
                complaint_text=payload["complaint_text"],
                case_id=payload["case_id"],
                channel=payload["channel"],
            )
            if payload.get("scenario") == SCENARIO_PENDING:
                rec = self._prime_pending_review(rec["complaint_id"])
            created.append({
                "complaint_id": rec["complaint_id"],
                "scenario": payload["scenario"],
                "task_status": rec["task_status"],
            })
        return created

    def _prime_pending_review(self, complaint_id: str) -> dict:
        """演示用：直接进入待人审且 AI=pending，便于验收补证引导。"""
        from app.service.trace import make_trace_event, utc_now

        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        ok = session_service.transition(sid, TaskStatus.pending_review)
        if not ok:
            raise RuntimeError(f"cannot prime pending_review for {sid}")
        rec = self._sync_from_session(rec)
        rec["verdict"] = VerdictType.pending
        rec["confidence"] = 0.35
        rec["response_draft"] = (
            "证据不足（缺完整录音/通话/短信），建议补证后再判定成立性。"
        )
        rec["stop_call_flag"] = False
        rec["trace_events"] = [
            make_trace_event(
                category="Agent",
                name="verdict",
                summary="证据不足 → pending（演示样例）",
                started_at=utc_now(),
                duration_ms=5,
            ),
        ]
        rec["agent_logs"].append(
            prepare_agent_log(
                "seed_pending",
                f"complaint_id={complaint_id}",
                "verdict=pending primed for V4 demo",
                duration_ms=1,
            )
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        return rec
    def list(self, page: int = 1, page_size: int = 20) -> dict:
        items = [self._sync_from_session(r) for r in self._cache.values()]
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return {"items": items[start:end], "total": total,
                "page": page, "page_size": page_size}

    def get(self, complaint_id: str) -> Optional[dict]:
        rec = self._cache.get(complaint_id)
        return self._sync_from_session(rec) if rec else None

    def get_or_raise(self, complaint_id: str) -> dict:
        rec = self.get(complaint_id)
        if rec is None:
            raise ComplaintNotFound(complaint_id)
        return rec

    def judge(self, complaint_id: str, run_graph: bool = False) -> dict:
        """研判入口。

        - run_graph=False：同步 stub（单测/冒烟默认路径）
        - run_graph=True：请走 async `judge_async`（FastAPI 路由已 await）；
          若在无事件循环的同步上下文调用，则内部 asyncio.run 执行真图。
        """
        if run_graph:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(self.judge_async(complaint_id, run_graph=True))
            raise RuntimeError(
                "run_graph=True inside a running event loop; await complaint_service.judge_async(...)"
            )
        return self._judge_stub(complaint_id)

    async def judge_async(self, complaint_id: str, run_graph: bool = False) -> dict:
        """异步研判入口（FastAPI 路由使用）。"""
        if run_graph:
            rec = self.get_or_raise(complaint_id)
            return await self._judge_with_graph(rec, complaint_id)
        return self._judge_stub(complaint_id)

    def _judge_stub(self, complaint_id: str) -> dict:
        """stub 路径：沿合法状态机推进到 pending_review，写 mock verdict。"""
        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        ok = session_service.transition(sid, TaskStatus.pending_review)
        if not ok:
            raise RuntimeError(f"cannot advance session {sid} to pending_review")
        rec = self._sync_from_session(rec)

        allowed = ["rag_doc_001"]
        rec["verdict"] = VerdictType.agent_violation
        rec["confidence"] = 0.92
        rec["response_draft"] = "经核查，坐席存在不当话术，建议向用户致歉并按流程升级处理。"
        rec["allowed_rag_doc_ids"] = allowed
        rec["regulation_refs"] = ["rag_doc_001"]
        rec["rag_source_ids"] = ["rag_doc_001"]
        rec["stop_call_flag"] = True
        # stub 落盘最小证据时间线，供 V4 有序证据清单演示
        from datetime import timedelta

        from app.service.trace import make_trace_event, utc_now

        t0 = utc_now() - timedelta(seconds=30)
        t1 = utc_now() - timedelta(seconds=10)
        rec["trace_events"] = [
            make_trace_event(
                category="MCP",
                name="biz.get_case",
                summary="拉取案件与催记摘要（mock）",
                started_at=t0,
                duration_ms=40,
            ),
            make_trace_event(
                category="RAG",
                name="rag_kb.search",
                summary="命中催收话术合规条文 rag_doc_001（mock）",
                started_at=t1,
                duration_ms=25,
            ),
        ]
        publisher.publish("complaint.judged", {
            "trace_id": f"judge-{complaint_id}",
            "complaint_id": complaint_id,
            "verdict": rec["verdict"].value,
        })
        # 11.2 停止致电联动：坐席违规 → 发布 case.risk.upgraded
        publisher.publish("case.risk.upgraded", {
            "trace_id": f"risk-{complaint_id}",
            "complaint_id": complaint_id,
            "reason": "agent_violation",
        })
        rec["agent_logs"].append(
            prepare_agent_log("judge_stub", f"complaint_id={complaint_id}",
                              f"verdict={rec['verdict'].value}", duration_ms=12)
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        return rec

    async def _judge_with_graph(self, rec: dict, complaint_id: str) -> dict:
        """run_graph=True：编译并执行真图，回写 verdict 等字段，并落单 trace_events。"""
        from langgraph.checkpoint.memory import MemorySaver

        from app.api.deps import get_llm_provider, get_mcp_client
        from app.graph.builder import build_complaint_graph
        from app.models.state import ComplaintSessionState
        from app.service.trace import (
            NodeTimer,
            OptionalLangfuseRun,
            OptionalLangSmithRun,
            filter_new_logs,
            llm_entry_to_event,
            make_trace_event,
            mcp_entry_to_event,
            snapshot_call_log_len,
        )

        sid = rec["session_id"]
        case_ids = list(rec.get("case_ids") or [])
        if not case_ids and rec.get("case_id"):
            case_ids = [str(rec["case_id"])]

        trace_id = f"trace-{complaint_id}"
        state_in = ComplaintSessionState(
            session_id=sid,
            trace_id=trace_id,
            complaint_id=complaint_id,
            complaint_text=rec.get("complaint_text", ""),
            case_ids=case_ids,
        ).model_dump()
        # MemorySaver：支持 human_review interrupt 挂起；API 侧本批不 resume，停在人审即可
        graph = build_complaint_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": sid}}

        mcp = get_mcp_client()
        llm = get_llm_provider()
        mcp_i0 = snapshot_call_log_len(mcp)
        llm_i0 = snapshot_call_log_len(llm)
        rec.setdefault("trace_events", [])
        run_events: list[dict] = []
        timer = NodeTimer()
        judge_inputs = {
            "complaint_id": complaint_id,
            "trace_id": trace_id,
            "session_id": sid,
        }
        ls_run = OptionalLangSmithRun("complaint_judge", judge_inputs)
        lf_run = OptionalLangfuseRun("complaint_judge", judge_inputs)
        interrupt_err: Exception | None = None
        final: dict = {}

        try:
            async for chunk in graph.astream(state_in, config, stream_mode="updates"):
                if not isinstance(chunk, dict):
                    continue
                for node_name, _update in chunk.items():
                    name = str(node_name)
                    if name.startswith("__"):
                        continue
                    run_events.append(
                        make_trace_event(
                            category="Agent",
                            name=name,
                            status="success",
                            duration_ms=timer.lap_ms(),
                            summary=f"node={name}",
                        )
                    )
            try:
                snap = await graph.aget_state(config)
                if snap and snap.values:
                    final = dict(snap.values)
            except Exception:  # noqa: BLE001
                final = {}
        except Exception as e:  # noqa: BLE001 — 含 GraphInterrupt：图停在人工断点
            interrupt_err = e
            final = {}
            try:
                snap = await graph.aget_state(config)
                if snap and snap.values:
                    final = dict(snap.values)
            except Exception:  # noqa: BLE001
                final = {}
            rec.setdefault("agent_logs", []).append(
                prepare_agent_log(
                    "judge_graph_interrupt",
                    f"complaint_id={complaint_id}",
                    f"paused_or_error={type(e).__name__}: {e}",
                    duration_ms=0,
                )
            )
            run_events.append(
                make_trace_event(
                    category="Agent",
                    name="human_review_or_error",
                    status="paused" if "Interrupt" in type(e).__name__ else "error",
                    duration_ms=timer.lap_ms(),
                    summary=f"{type(e).__name__}: {e}",
                )
            )

        # 落单 MCP / LLM / RAG（本轮 call_log 增量 → 工单存储）
        try:
            mcp_new = filter_new_logs(
                mcp.call_log, start_index=mcp_i0, session_id=sid, trace_id=trace_id,
            )
            for entry in mcp_new:
                run_events.append(mcp_entry_to_event(entry))
        except Exception:  # noqa: BLE001
            pass
        try:
            llm_new = filter_new_logs(
                llm.call_log, start_index=llm_i0, session_id=sid, trace_id=trace_id,
            )
            for entry in llm_new:
                run_events.append(llm_entry_to_event(entry))
        except Exception:  # noqa: BLE001
            pass

        if not any(e.get("category") == "Agent" for e in run_events):
            run_events.append(
                make_trace_event(
                    category="Agent",
                    name="judge_graph",
                    status="success" if interrupt_err is None else "paused",
                    duration_ms=0,
                    summary=f"complaint_id={complaint_id}",
                )
            )

        rec["trace_events"].extend(run_events)

        if ls_run.run_id:
            rec["langsmith_run_id"] = ls_run.run_id
        run_outputs = {
            "verdict": str((final or {}).get("verdict_draft") or "")[:200],
        }
        run_error = None if interrupt_err is None else f"{type(interrupt_err).__name__}"
        ls_run.end(outputs=run_outputs, error=run_error)
        lf_run.end(outputs=run_outputs, error=run_error)

        # 推进 session 状态
        ok = session_service.transition(sid, TaskStatus.pending_review)
        if not ok:
            raise RuntimeError(f"cannot advance session {sid} to pending_review")
        rec = self._sync_from_session(rec)

        # 从 graph 终态回写：优先 judgment_package 答辩稿，其次 verdict_draft
        pkg = final.get("judgment_package") if isinstance(final, dict) else None
        draft = final.get("verdict_draft") if isinstance(final, dict) else None

        response_draft = ""
        if pkg is not None:
            if hasattr(pkg, "response_draft"):
                response_draft = getattr(pkg, "response_draft", "") or ""
                if not response_draft:
                    response_draft = getattr(pkg, "judgment_report", "") or ""
            elif isinstance(pkg, dict):
                response_draft = str(
                    pkg.get("response_draft")
                    or pkg.get("judgment_report")
                    or pkg.get("verdict_report")
                    or ""
                )

        if draft is not None and hasattr(draft, "verdict"):
            rec["verdict"] = draft.verdict
            rec["confidence"] = float(getattr(draft, "confidence", 0.0) or 0.0)
            if not response_draft:
                response_draft = getattr(draft, "reasoning", "") or ""
        elif isinstance(draft, dict) and draft.get("verdict") is not None:
            v = draft.get("verdict")
            rec["verdict"] = VerdictType(v) if not isinstance(v, VerdictType) else v
            rec["confidence"] = float(draft.get("confidence") or 0.0)
            if not response_draft:
                response_draft = str(draft.get("reasoning") or "")
        else:
            rec["verdict"] = VerdictType.pending
            rec["confidence"] = 0.0

        rec["response_draft"] = response_draft or ""
        # 仍空时给出可人审的说明（避免前端只显示「-」且无上下文）
        if not (rec["response_draft"] or "").strip():
            v_val = rec["verdict"].value if hasattr(rec["verdict"], "value") else str(rec["verdict"])
            rec["response_draft"] = (
                f"【系统提示】研判图未产出答辩草稿（verdict={v_val}）。"
                "请检查 USE_MOCK_LLM=false 与 LLM Key，或查看 agent-logs / 服务日志中的 real_llm_invoke。"
            )

        from app.service.evidence_refs import extract_citations_from_graph_final

        allowed, regs, srcs = extract_citations_from_graph_final(
            final if isinstance(final, dict) else {}
        )
        rec["allowed_rag_doc_ids"] = allowed
        rec["regulation_refs"] = regs
        rec["rag_source_ids"] = srcs
        rec["stop_call_flag"] = rec["verdict"] == VerdictType.agent_violation
        v_val = rec["verdict"].value if hasattr(rec["verdict"], "value") else str(rec["verdict"])
        publisher.publish("complaint.judged", {
            "trace_id": f"judge-{complaint_id}",
            "complaint_id": complaint_id,
            "verdict": v_val,
        })
        if rec["stop_call_flag"]:
            publisher.publish("case.risk.upgraded", {
                "trace_id": f"risk-{complaint_id}",
                "complaint_id": complaint_id,
                "reason": "agent_violation",
            })
        rec["agent_logs"].append(
            prepare_agent_log("judge_graph", f"complaint_id={complaint_id}",
                              f"verdict={v_val}", duration_ms=0)
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        return rec

    def get_trace(self, complaint_id: str) -> dict:
        """V3 主契约：已落盘的研判链路时间线。"""
        from app.service.trace import build_langsmith_url

        rec = self.get_or_raise(complaint_id)
        events = list(rec.get("trace_events") or [])

        def _sort_key(e: dict) -> tuple:
            ts = e.get("started_at")
            if hasattr(ts, "timestamp"):
                return (ts.timestamp(), e.get("name") or "")
            return (str(ts or ""), e.get("name") or "")

        events_sorted = sorted(events, key=_sort_key)
        return {
            "complaint_id": complaint_id,
            "events": events_sorted,
            "langsmith_url": build_langsmith_url(rec.get("langsmith_run_id")),
        }

    def get_status(self, complaint_id: str) -> dict:
        rec = self.get_or_raise(complaint_id)
        return {"complaint_id": complaint_id, "task_status": rec["task_status"],
                "progress": 100 if rec["verdict"] is not None else 0}

    def get_verdict(self, complaint_id: str) -> dict:
        rec = self.get_or_raise(complaint_id)
        return {
            "complaint_id": complaint_id,
            "verdict": rec["verdict"],
            "confidence": rec["confidence"],
            "response_draft": rec["response_draft"],
            "regulation_refs": rec["regulation_refs"],
            "rag_source_ids": rec["rag_source_ids"],
            "allowed_rag_doc_ids": list(rec.get("allowed_rag_doc_ids") or []),
            "stop_call_flag": bool(rec.get("stop_call_flag", False)),
        }

    def get_agent_logs(self, complaint_id: str) -> dict:
        rec = self.get_or_raise(complaint_id)
        return {"complaint_id": complaint_id, "logs": rec["agent_logs"]}

    def review(self, complaint_id: str, review_action: ReviewAction,
               reviewer: str = "operator", comment: Optional[str] = None,
               reason: Optional[str] = None) -> dict:
        from app.service.review_service import review_service
        return review_service.submit(
            complaint_id, review_action, reviewer=reviewer, comment=comment,
            reason=reason,
        )

    def submit_disposition_verdict(
        self,
        complaint_id: str,
        establishment: VerdictType,
        reviewer: str = "operator",
        reason: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """V4 定责：成立性三选一 → pending_callback。"""
        allowed = {
            VerdictType.agent_violation,
            VerdictType.user_misunderstanding,
            VerdictType.compliant_but_poor_experience,
        }
        if establishment not in allowed:
            raise ValueError("成立结论必须为坐席违规/用户误解/体验差之一，不能为 pending")

        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        cur = session_service.get_status(sid)
        if cur not in (
            TaskStatus.pending_review.value,
            TaskStatus.pending_senior_review.value,
        ):
            raise ValueError(f"当前状态不允许定责: {cur}")

        ai = rec.get("ai_verdict")
        if ai is None:
            ai = rec.get("verdict")
        ai_val = ai.value if hasattr(ai, "value") else (str(ai) if ai is not None else None)
        est_val = establishment.value
        if ai_val != est_val:
            if reason is None or not str(reason).strip():
                raise ValueError("与 AI 初选不一致时必须填写改判原因")

        if rec.get("ai_verdict") is None and rec.get("verdict") is not None:
            rec["ai_verdict"] = rec["verdict"]

        rec["disposition_verdict"] = establishment
        rec["disposition_reason"] = (str(reason).strip() if reason else None) or None
        rec["verdict"] = establishment
        if est_val != VerdictType.agent_violation.value:
            rec["stop_call_flag"] = False
        else:
            rec["stop_call_flag"] = True

        if not session_service.transition(sid, TaskStatus.pending_callback):
            raise RuntimeError(f"cannot transition to pending_callback from {cur}")
        rec = self._sync_from_session(rec)
        rec["agent_logs"].append(
            prepare_agent_log(
                "disposition_verdict",
                f"complaint_id={complaint_id}",
                (
                    f"establishment={est_val} ai={ai_val} "
                    f"reason={rec.get('disposition_reason')} reviewer={reviewer}"
                    + (f" comment={comment}" if comment else "")
                ),
                duration_ms=0,
            )
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        return rec

    def submit_disposition_callback(
        self,
        complaint_id: str,
        reviewer: str = "operator",
        note: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> dict:
        """V4 回电处置：发布 callback.requested → resolved；幂等不重复发。"""
        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        cur = session_service.get_status(sid)

        # 已安排：幂等成功
        if rec.get("callback_scheduled") and cur in (
            TaskStatus.resolved.value,
            TaskStatus.closed.value,
        ):
            return rec

        if cur != TaskStatus.pending_callback.value:
            raise ValueError(f"当前状态不允许回电处置: {cur}")

        establishment = rec.get("disposition_verdict") or rec.get("verdict")
        est_val = (
            establishment.value
            if hasattr(establishment, "value")
            else (str(establishment) if establishment is not None else None)
        )
        if est_val is None:
            raise ValueError("缺少成立性结论，无法安排回电")

        note_text = (str(note or comment or "").strip() or None)

        publisher.publish(
            "callback.requested",
            {
                "complaint_id": complaint_id,
                "establishment": est_val,
                "callback_required": True,
                "note": note_text,
                "operator": reviewer,
                "session_id": sid,
            },
        )
        rec["callback_scheduled"] = True
        rec["callback_note"] = note_text

        # V4 段 C：坐席违规 → 质检事件（空证据仍发）
        if est_val == VerdictType.agent_violation.value:
            from app.service.ordered_evidence import build_ordered_evidence
            from app.service.security import mask_before_persist

            evidence = build_ordered_evidence(rec)
            reason = (
                (rec.get("disposition_reason") or "").strip()
                or (rec.get("response_draft") or "").strip()
                or "人审确认坐席违规"
            )
            reason = mask_before_persist(reason[:800])
            complaint_summary = mask_before_persist(
                str(rec.get("complaint_text") or "")[:500]
            )
            publisher.publish(
                "qc.violation.reported",
                {
                    "complaint_id": complaint_id,
                    "establishment": est_val,
                    "deduction_reason": reason,
                    "complaint_summary": complaint_summary,
                    "evidence": evidence,
                    "operator": reviewer,
                },
            )
            # 兼容双发（design D3）
            publisher.publish(
                "case.risk.upgraded",
                {
                    "complaint_id": complaint_id,
                    "reason": "agent_violation_confirmed",
                    "source": "disposition_callback",
                },
            )

        if not session_service.transition(sid, TaskStatus.resolved):
            raise RuntimeError(f"cannot transition to resolved from {cur}")
        rec = self._sync_from_session(rec)
        rec["agent_logs"].append(
            prepare_agent_log(
                "disposition_callback",
                f"complaint_id={complaint_id}",
                f"establishment={est_val} operator={reviewer} note={note_text}",
                duration_ms=0,
            )
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        return rec

    def close(self, complaint_id: str, closer: str = "operator",
              reason: Optional[str] = None) -> dict:
        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        cur = session_service.get_status(sid)
        if cur == TaskStatus.closed.value:
            return rec

        if cur == TaskStatus.pending_callback.value:
            raise ValueError("须先完成回电处置安排，不可关单")
        if cur != TaskStatus.resolved.value:
            raise ValueError(
                f"当前状态不可关单（须定责且回电完成后为 resolved）: {cur}"
            )
        # 凡投诉：关单前必须已安排回电（含旧 approve 路径自动标记）
        if not rec.get("callback_scheduled"):
            raise ValueError("须先完成回电处置安排，不可关单")

        if not session_service.transition(sid, TaskStatus.closed):
            raise RuntimeError(f"cannot close session {sid}")
        rec = self._sync_from_session(rec)
        publisher.publish("complaint.closed", {
            "complaint_id": complaint_id, "closer": closer, "reason": reason,
            "closed_at": rec["updated_at"].isoformat(),
        })
        return rec


# 全局单例
complaint_service = ComplaintService()

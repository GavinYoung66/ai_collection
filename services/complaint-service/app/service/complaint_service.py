"""投诉工单服务门面（批次8 收敛）。

批次7 内存 stub 收敛为薄门面：真相源为 session_service，
路由层接口签名保持不变；cache 仅存 verdict 等业务字段，task_status 始终回写自 session。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog

from app.config import settings
from app.events.publisher import publisher
from app.models.state import ReviewAction, TaskStatus, VerdictType
from app.service.rate_limiter import rate_limiter
from app.service.security import prepare_agent_log
from app.service.session_service import session_service

logger = structlog.get_logger()


class ComplaintPersistError(Exception):
    """投诉工单落 PostgreSQL 失败（路由须返回明确错误，禁止静默成功）。"""

# 进程级 MemorySaver：judge 的 checkpointer 按 thread_id=complaint_id 留在投诉进程内，
# 禁止请求结束就丢弃。review approve 时对同一线程 Command(resume="approve") 恢复图。
_graph_checkpointer = None

# G2 幂等守卫：resume 前等图进入 human_review 断点的轮询参数。
# judge 真图含多轮真实 LLM 调用（reflection/verdict/generate_package 等），
# 正常 ~40s 内入断点；给 120s 上限覆盖慢模型/重试，间隔 1s 不密集打 checkpoint。
_RESUME_WAIT_TIMEOUT_S = 120.0
_RESUME_WAIT_INTERVAL_S = 1.0

# G1b 图执行在途登记：同一 complaint_id 同一时刻只允许一张图在跑。
# data-platform 订阅者与验收脚本双重驱动 + judge 内部 HTTP 5s 超时重试，
# 会在真图（~40s）结束前重复 POST /judge；若并发跑第二遍，同一 thread_id 的
# checkpoint 会被两路 astream 交替覆写（分叉），随后 review 的 Command(resume)
# 恢复不到 human_review 断点，sample_backflow/write_session 永不执行。
_graph_inflight: set[str] = set()

# resume 互斥：同工单可能被多处 review approve 调度（订阅者 + 验收脚本），
# 并发 resume 会双跑 human_review→sample_backflow；按 complaint_id 串行化。
_resume_locks: dict[str, asyncio.Lock] = {}

# _release_resume_lock 探测 asyncio.Lock._waiters 时的"无此属性"哨兵
_RESUME_LOCK_ATTR_MISSING = object()


def _resume_lock(complaint_id: str) -> asyncio.Lock:
    lock = _resume_locks.get(complaint_id)
    if lock is None:
        lock = asyncio.Lock()
        _resume_locks[complaint_id] = lock
    return lock


def _release_resume_lock(complaint_id: str, lock: asyncio.Lock) -> None:
    """无排队等待者时回收 per-complaint 锁，避免 dict 随工单量无界增长。

    调用时机硬约束：必须在仍持有该锁、且调用后到锁实际释放之间无 await。
    协程一旦从 ``_resume_lock`` 取到本实例，下一句即 ``async with`` 排队，
    中间无 await，故此刻不在 ``_waiters`` 里的协例不可能持有本实例，
    删除登记不会导致后来者另建新锁而绕过互斥。
    运行时无 ``_waiters`` 属性时不回收（退化为旧行为，仅缓慢占内存）。
    CPython 3.11 中 ``_waiters`` 空闲为 None、有等待者时为非空 deque。
    """
    waiters = getattr(lock, "_waiters", _RESUME_LOCK_ATTR_MISSING)
    if waiters is not _RESUME_LOCK_ATTR_MISSING and not waiters:
        _resume_locks.pop(complaint_id, None)


def _get_graph_checkpointer():
    """惰性初始化进程级 MemorySaver（避免 import 时加载 langgraph）。"""
    global _graph_checkpointer
    if _graph_checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        _graph_checkpointer = MemorySaver()
    return _graph_checkpointer


def is_graph_inflight(complaint_id: str) -> bool:
    """同工单真图是否正在执行（路由层幂等 no-op 判定）。"""
    return complaint_id in _graph_inflight


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

    async def create_async(self, complaint_text: str, session_id: Optional[str] = None,
                           case_id: Optional[str] = None, channel: str = "phone") -> dict:
        """创建入口（FastAPI 路由使用）：内存会话 + 真路径 PostgreSQL 持久化。

        - USE_MOCK_MCP=true：仅内存（mock 主路径，测试不依赖 PG）；
        - USE_MOCK_MCP=false：同事务 SELECT 幂等 + INSERT complaints，
          成功 commit、失败 rollback 并抛 ComplaintPersistError（明确错误，不静默）。
        """
        rec = self.create(
            complaint_text, session_id=session_id, case_id=case_id, channel=channel
        )
        if not getattr(settings, "USE_MOCK_MCP", True):
            await self._persist_complaint_row(rec, complaint_text)
        return rec

    async def _persist_complaint_row(self, rec: dict, complaint_text: str) -> None:
        """将工单写入 public.complaints（ORM 映射，见 app.models.complaint.Complaint）。

        - complaint_id 现有行为等于 case_id，complaints.id 显式取 case_id，
          与 GET /api/v1/complaints/{complaint_id}、快照回写 complaint_id 对齐；
        - 幂等：同 case_id 且 is_delete='0' 已有行则跳过，不产生第二张工单；
        - session_id 不落库（闭环创建不传通话 session_id，避免 FK 指向随机 uuid）。
        """
        import uuid as _uuid

        from sqlalchemy import select

        from app.models.complaint import Complaint
        from common.db.session import async_session_factory

        cid = str(rec["complaint_id"])
        case_id = str(rec.get("case_id") or cid)
        try:
            case_uuid = _uuid.UUID(case_id)
        except ValueError as e:
            raise ComplaintPersistError(f"case_id 非法 UUID: {case_id}") from e

        db = async_session_factory()
        try:
            async with db:
                existing = await db.execute(
                    select(Complaint)
                    .where(Complaint.case_id == case_uuid)
                    .limit(1)
                )
                row = existing.scalar_one_or_none()
                if row is not None:
                    if row.is_delete == "0":
                        # 幂等 no-op，不插第二行
                        logger.info("complaint_persist_idempotent_hit", case_id=case_id)
                        return
                    # 软删行存在：恢复为活跃行（软删后重建同 id 工单）
                    row.is_delete = "0"
                    row.complaint_text = complaint_text
                    row.status = "pending"
                    await db.commit()
                    logger.info("complaint_persist_restored", case_id=case_id)
                    return
                db.add(
                    Complaint(
                        id=case_uuid,
                        case_id=case_uuid,
                        complaint_text=complaint_text,
                        status="pending",
                    )
                )
                await db.commit()
                logger.info("complaint_persisted", complaint_id=cid, case_id=case_id)
        except ComplaintPersistError:
            raise
        except Exception as e:
            # 连接失败/约束违反等：显式回滚未提交事务，禁止留半截行
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.exception("complaint_persist_failed", case_id=case_id, error=str(e))
            raise ComplaintPersistError(f"投诉工单落库失败: {e}") from e

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
            # G1b 在途守卫：同线程已有真图在跑（HTTP 5s 超时重试/订阅者与脚本双驱动）
            # → 直接返回当前态，不并发跑第二遍图（避免 checkpoint 分叉导致 resume 失效）
            if complaint_id in _graph_inflight:
                logger.info("judge_skip_graph_inflight", complaint_id=complaint_id)
                return self._sync_from_session(rec)
            # G5 排队 deadline：入队超 10min（queue_start 已失效）→ A5010 转人审，不进图
            if not await rate_limiter.check_queue_deadline(complaint_id):
                return self._handle_queue_timeout(rec)
            out = rec
            _graph_inflight.add(complaint_id)
            try:
                out = await self._judge_with_graph(rec, complaint_id)
            finally:
                _graph_inflight.discard(complaint_id)
            # 5.4：judge 返回后同栈检查；仅 next 含 human_review 且开关开才 resume
            await self._maybe_sim_auto_review(complaint_id)
            return self.get(complaint_id) or out
        return self._judge_stub(complaint_id)

    async def _maybe_sim_auto_review(self, complaint_id: str) -> None:
        """SIM_AUTO_REVIEW：同栈、无轮询；仅 next 含 human_review 才 Command(resume)。"""
        from app.config import settings as _settings

        if not getattr(_settings, "SIM_AUTO_REVIEW", False):
            return
        from langgraph.types import Command

        from app.graph.builder import HUMAN_REVIEW, build_complaint_graph

        try:
            graph = build_complaint_graph(checkpointer=_get_graph_checkpointer())
            config = {"configurable": {"thread_id": complaint_id}}
            snap = await graph.aget_state(config)
            nxt = tuple(getattr(snap, "next", None) or ())
            if HUMAN_REVIEW not in nxt:
                logger.info(
                    "sim_auto_review_skip_no_breakpoint",
                    complaint_id=complaint_id,
                    next=list(nxt),
                )
                return
            logger.info("sim_auto_review_resume", complaint_id=complaint_id)
            await graph.ainvoke(Command(resume="approve"), config=config)
        except Exception:  # noqa: BLE001
            logger.exception("sim_auto_review_failed", complaint_id=complaint_id)

    def _handle_queue_timeout(self, rec: dict, node: str = "judge_async") -> dict:
        """排队超 10min：A5010 + pending_review（HTTP 仍 200，前端按状态展示）。"""
        from app.models.state import ExceptionInfo
        from app.service.degrade_flow import QUEUE_TIMEOUT_SECONDS, degrade_flow

        sid = rec["session_id"]
        degrade_flow.handle_queue_timeout(
            QUEUE_TIMEOUT_SECONDS,
            trace_id=f"trace-{rec['complaint_id']}",
            node=node,
        )  # 发 complaint.degraded/A5010（真实 trace_id + 来源 node）
        session_service.transition(sid, TaskStatus.pending_review)
        rec = self._sync_from_session(rec)
        exc_info = ExceptionInfo(
            error_type="queue_timeout",
            message=f"排队超过 {QUEUE_TIMEOUT_SECONDS // 60}min 未获执行槽",
            node=node,
        ).model_dump()
        rec["exception_info"] = exc_info
        session = session_service.get_session(sid)
        if session is not None:
            session["exception_info"] = exc_info
        return rec

    async def fail_queue_timeout(self, complaint_id: str) -> dict:
        """排队超时公共入口（main.py 队列消费循环对 expired 工单调用的）：
        A5010 转 pending_review，并释放维5幂等锁（工单已出队，允许后续重判）。"""
        rec = self.get_or_raise(complaint_id)
        rec = self._handle_queue_timeout(rec, node="queue_consumer")
        await rate_limiter.release_complaint_lock(complaint_id)
        return rec

    async def fail_shutdown(self, complaint_id: str) -> bool:
        """进程关停取消在途研判时的 best-effort 状态兜底（P3-4）。

        shutdown 取消排队研判任务后，session 可能滞留 judging：此处把流水线内
        会话经合法多步流转推进到 pending_review，并写
        exception_info(error_type=shutdown_cancelled, node=queue_consumer)。

        - 已在 pending_review：幂等 no-op，返回 True；
        - 已 resolved/closed 等终态：transition 返回 False，MUST NOT 回退或覆盖；
        - 不发 A5010（本场景是进程关停，非排队/执行 deadline 超时）。

        注意：本协程体内不含 await 挂起点（全部为内存态操作），可在
        CancelledError except 块中被原子地 await 完成，不会被取消信号二次打断；
        批次4 换成 Redis 持久化 session 后，调用处需改用 asyncio.shield 包裹。
        """
        from app.models.state import ExceptionInfo

        rec = self.get_or_raise(complaint_id)
        sid = rec["session_id"]
        session = session_service.get_session(sid)
        prev_status = session["task_status"].value if session is not None else None
        if prev_status == TaskStatus.pending_review.value:
            # 已到人审（可能刚写完自己的 exception_info 即被取消）：幂等 no-op，不覆盖
            return True
        ok = session_service.transition(sid, TaskStatus.pending_review)
        if not ok:
            return False
        rec = self._sync_from_session(rec)
        exc_info = ExceptionInfo(
            error_type="shutdown_cancelled",
            message="服务关停，在途研判被取消，已转人审兜底",
            node="queue_consumer",
        ).model_dump()
        rec["exception_info"] = exc_info
        session = session_service.get_session(sid)
        if session is not None:
            session["exception_info"] = exc_info
        return True

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

    async def _collect_graph_updates(
        self, graph, state_in: dict, config: dict, run_events: list, timer
    ) -> None:
        """消费 graph astream updates，逐节点落 Agent trace（供 wait_for 包执行 deadline）。"""
        from app.service.trace import make_trace_event

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

    async def _judge_with_graph(self, rec: dict, complaint_id: str) -> dict:
        """run_graph=True：编译并执行真图，回写 verdict 等字段，并落单 trace_events。

        使用进程级 MemorySaver（非请求级），使 checkpointer 按 thread_id=complaint_id
        留在投诉进程内。review approve 时对同一线程 Command(resume="approve") 恢复图。
        """
        from langgraph.errors import GraphInterrupt

        from app.api.deps import get_llm_provider, get_mcp_client
        from app.graph.builder import build_complaint_graph
        from app.llm.provider import CircuitBreakerOpenError
        from app.models.state import ComplaintSessionState, ExceptionInfo
        from common.llm.reliability import LLMRateLimitError
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
        # 进程级 checkpointer：thread_id=complaint_id，judge 停在 human_review 后状态不丢弃，
        # review approve 时用同一线程 Command(resume="approve") 恢复图进 sample_backflow
        graph = build_complaint_graph(checkpointer=_get_graph_checkpointer())
        config = {"configurable": {"thread_id": complaint_id}}

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
        graph_error: Exception | None = None
        graph_error_type: str = ""
        final: dict = {}

        try:
            # G5 执行 deadline：exec_start(30min TTL) 已写；wait_for 超时 → A5010
            await rate_limiter.start_execution(complaint_id)
            try:
                await asyncio.wait_for(
                    self._collect_graph_updates(
                        graph, state_in, config, run_events, timer
                    ),
                    timeout=rate_limiter.exec_timeout_seconds,
                )
            finally:
                # 5.3：exec key 在 ainvoke 完成（含超时/异常）时即删，与 queue_start 独立
                await rate_limiter.finish_execution(complaint_id)
        except GraphInterrupt as e:
            # 正常人审挂起：转 pending_review，不写 exception_info（spec session-service）
            interrupt_err = e
        except TimeoutError as e:
            # G5 执行超 30min：发 complaint.degraded/A5010，再走 G3 超时分支
            from app.service.degrade_flow import GLOBAL_TIMEOUT_SECONDS, degrade_flow

            deadline = rate_limiter.exec_timeout_seconds
            # 语义上即「撞满 30min 全局上限」，按常量阈值触发，便于测试缩短实际 wait_for
            degrade_flow.handle_global_timeout(
                float(GLOBAL_TIMEOUT_SECONDS),
                trace_id=trace_id,
                node="graph",
            )
            logger.warning("judge_exec_timeout", complaint_id=complaint_id, deadline=deadline)
            graph_error = TimeoutError(f"exec deadline {deadline}s exceeded")
            graph_error_type = "timeout"
        except (CircuitBreakerOpenError, RuntimeError) as e:
            # 运行时/韧性错误：pending_review + exception_info
            graph_error = e
            if isinstance(e, CircuitBreakerOpenError):
                graph_error_type = "circuit_open"
            elif isinstance(e, LLMRateLimitError):
                # 内部模型维限流（非用户维 B0006）：转人审，落明确错误类型
                graph_error_type = "llm_rate_limited"
            else:
                graph_error_type = type(e).__name__
        except Exception as e:  # noqa: BLE001 — 兜底：unknown_error + pending_review
            graph_error = e
            graph_error_type = "unknown_error"

        # 保留部分终态快照（成功与异常路径统一）
        try:
            snap = await graph.aget_state(config)
            if snap and snap.values:
                final = dict(snap.values)
        except Exception:  # noqa: BLE001
            final = {}

        if interrupt_err is not None or graph_error is not None:
            e = interrupt_err if interrupt_err is not None else graph_error
            assert e is not None
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
                    status="paused" if interrupt_err is not None else "error",
                    duration_ms=timer.lap_ms(),
                    summary=f"{type(e).__name__}: {e}",
                )
            )

        if graph_error is not None:
            exc_info = ExceptionInfo(
                error_type=graph_error_type, message=str(graph_error)[:300], node="graph"
            ).model_dump()
            rec["exception_info"] = exc_info
            session = session_service.get_session(sid)
            if session is not None:
                session["exception_info"] = exc_info

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
            status = "success"
            if interrupt_err is not None and graph_error is None:
                status = "paused"
            elif graph_error is not None:
                status = "error"
            run_events.append(
                make_trace_event(
                    category="Agent",
                    name="judge_graph",
                    status=status,
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
        run_err = interrupt_err if interrupt_err is not None else graph_error
        run_error = None if run_err is None else f"{type(run_err).__name__}"
        ls_run.end(outputs=run_outputs, error=run_error)
        lf_run.end(outputs=run_outputs, error=run_error)

        # 推进 session 状态
        # G3 幂等守卫：双驱动下 review approve 可能已把 session 推进到 resolved，
        # 此处（多为重入/超时重试的 judge 完成）再推进 pending_review 属非法迁移，
        # 幂等跳过而非抛 500——resolved 已是人审确认终态，不回退。
        _cur_status = session_service.get_status(sid)
        if _cur_status in (
            TaskStatus.pending_review.value,
            TaskStatus.resolved.value,
            TaskStatus.closed.value,
            TaskStatus.archived.value,
        ):
            logger.info(
                "judge_advance_skip_already_reviewed",
                complaint_id=complaint_id,
                current_status=_cur_status,
            )
        else:
            ok = session_service.transition(sid, TaskStatus.pending_review)
            if not ok:
                raise RuntimeError(f"cannot advance session {sid} to pending_review")
        # spec：judge 返回 SHALL 含 thread_id（= complaint_id），
        # 供 review approve 用同一线程 Command(resume="approve") 恢复图
        rec["thread_id"] = complaint_id
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
        rec = review_service.submit(
            complaint_id, review_action, reviewer=reviewer, comment=comment,
            reason=reason,
        )
        # 3.5: approve 时对同一线程 Command(resume="approve")，恢复图进入 sample_backflow → write_session
        if review_action == ReviewAction.approve:
            self._schedule_graph_resume(complaint_id, rec.get("session_id"))
        return rec

    def _schedule_graph_resume(self, complaint_id: str, session_id: str | None) -> None:
        """调度图恢复为后台任务（非阻塞）。不阻塞 review 路由响应。"""
        if not session_id:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._resume_graph_thread(complaint_id, session_id))
        except RuntimeError:
            # 无事件循环（如同步单测）：跳过图恢复，不阻塞
            logger.warning("no_event_loop_for_graph_resume", complaint_id=complaint_id)

    async def _resume_graph_thread(self, complaint_id: str, session_id: str) -> None:
        """review approve 后恢复图线程 → sample_backflow → write_session。

        使用 judge 时同一进程级 MemorySaver 和 thread_id=complaint_id。
        不改图节点、不新做路由。session_id 仅用于日志上下文。

        G2 幂等守卫：data-platform 订阅者与验收脚本双重驱动时，review approve
        可能早于 judge 图跑到 human_review 断点，此时 checkpoint 尚无挂起态，
        直接 Command(resume) 会空转导致 sample_backflow/write_session 永不执行。
        故 resume 前先等本工单的 judge 图执行结束，再等 checkpoint 停在
        human_review 断点（最多轮询 _RESUME_WAIT_TIMEOUT_S）；未入断点则不发
        resume，避免污染 checkpoint。
        """
        from langgraph.types import Command
        from app.graph.builder import HUMAN_REVIEW, build_complaint_graph
        try:
            graph = build_complaint_graph(checkpointer=_get_graph_checkpointer())
            config = {"configurable": {"thread_id": complaint_id}}

            # G2a：先等 judge 图执行结束（judge 优先，resume 不抢执行权）
            inflight_waited = 0.0
            while complaint_id in _graph_inflight and inflight_waited < _RESUME_WAIT_TIMEOUT_S:
                await asyncio.sleep(0.2)
                inflight_waited += 0.2
            if complaint_id in _graph_inflight:
                logger.warning(
                    "graph_resume_inflight_timeout",
                    complaint_id=complaint_id,
                    session_id=session_id,
                    waited_s=round(inflight_waited, 3),
                )

            # G2b：等图真正停在 human_review 断点（next 含 human_review 才是可恢复态）。
            # 整段 poll+resume 持 resume 锁，避免订阅者与脚本各调度一次时并发双跑。
            resume_lock = _resume_lock(complaint_id)
            async with resume_lock:
                try:
                    # N6：双驱动（订阅者 + HTTP）争同一把锁，落败路拿到锁时
                    # 先到的一路已消费 human_review 断点：next 不再含 human_review，
                    # 且 human_review 结果已写入 state。立即返回，不空轮询 120s 刷 warning。
                    first_snap = await graph.aget_state(config)
                    first_next = tuple(getattr(first_snap, "next", None) or ())
                    first_values = getattr(first_snap, "values", None) or {}
                    hr_value = first_values.get("human_review")
                    hr_action = (
                        hr_value.get("review_action")
                        if isinstance(hr_value, dict)
                        else getattr(hr_value, "review_action", None)
                    )
                    already_reviewed = bool(
                        HUMAN_REVIEW not in first_next and hr_action
                    )
                    if already_reviewed:
                        logger.info(
                            "graph_resume_skip_already_resumed",
                            complaint_id=complaint_id,
                            session_id=session_id,
                            inflight_waited_s=round(inflight_waited, 3),
                        )
                        return

                    waited = 0.0
                    entered = False
                    while waited < _RESUME_WAIT_TIMEOUT_S:
                        snap = await graph.aget_state(config)
                        nxt = tuple(getattr(snap, "next", None) or ())
                        if HUMAN_REVIEW in nxt:
                            entered = True
                            break
                        await asyncio.sleep(_RESUME_WAIT_INTERVAL_S)
                        waited += _RESUME_WAIT_INTERVAL_S
                    if not entered:
                        logger.warning(
                            "graph_resume_no_interrupt_after_wait",
                            complaint_id=complaint_id,
                            session_id=session_id,
                            waited_s=waited,
                        )
                        return

                    await graph.ainvoke(Command(resume="approve"), config=config)
                finally:
                    # N4：持锁末尾回收（其后仅锁释放、无 await，见函数 docstring 约束）
                    _release_resume_lock(complaint_id, resume_lock)
            logger.info(
                "graph_resumed_after_approve",
                complaint_id=complaint_id,
                session_id=session_id,
                inflight_waited_s=round(inflight_waited, 3),
                waited_s=round(waited, 3),
                entered_interrupt=entered,
            )
        except Exception:
            logger.exception(
                "graph_resume_failed",
                complaint_id=complaint_id,
                session_id=session_id,
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

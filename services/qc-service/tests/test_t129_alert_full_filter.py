"""T129：call_qc query_qc_alerts 告警查询全量过滤（数据层下推）。

整改前缺陷：MCP server 只取 list_alerts 前 100 条（page=1, page_size=100）
后在内存按 qc_record_id 过滤——目标告警位于时间倒序第 101 条之后被静默漏报。

整改形态（数据层过滤为主）：
- AlertRepo.list_alerts 新增 qc_record_id 参数，SQL WHERE 下推（count 同步生效）；
- MCP server 调用点传入 record.id；内存匹配保留为纵深防御。

验收（spec「告警查询全量过滤」）：目标告警位于结果集后段（第 101 条之后）仍可查得。
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.mcp.server import CallQcServer
from app.models.qc import QcRecord
from app.repositories.alert_repo import AlertRepo

SESSION = "sess_t129"
MASKED_PHONE = "13812341234"


class FakeQualityRepo:
    """_load_record 依赖替身：按 session_id 返回质检记录。"""

    def __init__(self, record: QcRecord) -> None:
        self.record = record

    async def get_by_session_id(self, session_id: str) -> QcRecord | None:
        if self.record is not None and self.record.session_id == session_id:
            return self.record
        return None


class RecordingAlertRepo:
    """T129 替身：模拟数据层过滤语义——qc_record_id 在 WHERE 生效后再倒序分页。

    调用方不传 qc_record_id 时（旧形态），在未过滤全集上倒序分页，
    复现"目标位于第 101 条之后被静默漏报"的缺陷条件。记录每次调用 kwargs
    供数据层过滤形态断言。
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    async def list_alerts(
        self, *, page, page_size, status=None, session_id=None, qc_record_id=None
    ):
        self.calls.append(
            {
                "page": page,
                "page_size": page_size,
                "status": status,
                "session_id": session_id,
                "qc_record_id": qc_record_id,
            }
        )
        rows = list(self.rows)
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        if qc_record_id is not None:
            rows = [r for r in rows if r.get("qc_record_id") == str(qc_record_id)]
        rows.sort(key=lambda r: r["create_time"], reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total


def _record(*, session_id: str, case_id) -> QcRecord:
    row = QcRecord(
        session_id=session_id,
        compliance_score=80,
        risk_level="high",
        qc_result="pending_review",
        qc_status="PENDING_REVIEW",
    )
    row.id = uuid4()
    row.case_id = case_id
    return row


def _build_rows(*, wanted_id: str, other_id: str, wanted_count: int, other_count: int) -> list[dict]:
    """构造告警全集：other 较新、wanted 较旧 → 时间倒序下 wanted 排在后段。"""
    base = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    for i in range(other_count):
        rows.append(
            {
                "alert_id": f"other-{i}",
                "qc_record_id": other_id,
                "session_id": SESSION,
                "evidence": f"干扰告警 {i}",
                "create_time": base + timedelta(seconds=i),
            }
        )
    for i in range(wanted_count):
        rows.append(
            {
                "alert_id": f"target-{i}",
                "qc_record_id": wanted_id,
                "session_id": SESSION,
                "evidence": f"目标告警 手机{MASKED_PHONE} {i}",
                "create_time": base - timedelta(seconds=other_count + 1 - i),
            }
        )
    return rows


def _server_with(repo: RecordingAlertRepo, record: QcRecord) -> CallQcServer:
    return CallQcServer(quality_repo=FakeQualityRepo(record), alert_repo=repo)


class TestT129AlertFullFilter:
    @pytest.mark.asyncio
    async def test_target_beyond_100th_still_returned(self):
        """spec 场景：目标告警位于时间倒序第 101~105 条，查询仍返回，无静默漏报。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        other_id = str(uuid4())
        wanted_id = str(record.id)
        rows = _build_rows(wanted_id=wanted_id, other_id=other_id, wanted_count=5, other_count=100)
        server = _server_with(RecordingAlertRepo(rows), record)

        items = await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )

        assert len(items) == 5, "5 条目标告警（位于第 101 条之后）全部可查得"
        assert all(item["qc_record_id"] == wanted_id for item in items)
        assert "target-4" in {item["alert_id"] for item in items}

    @pytest.mark.asyncio
    async def test_repo_receives_qc_record_id_param(self):
        """数据层过滤形态断言：MCP server 调用 list_alerts 时传入 qc_record_id。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        rows = _build_rows(
            wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=1, other_count=3
        )
        repo = RecordingAlertRepo(rows)
        server = _server_with(repo, record)

        await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )

        assert len(repo.calls) == 1
        assert str(repo.calls[0]["qc_record_id"]) == str(record.id), (
            "qc_record_id MUST 下推到数据层（list_alerts 参数），而非仅内存过滤"
        )

    def test_alert_repo_signature_has_qc_record_id(self):
        """数据层过滤形态断言（真实 AlertRepo）：list_alerts 签名含 qc_record_id 参数。"""
        params = inspect.signature(AlertRepo.list_alerts).parameters
        assert "qc_record_id" in params, "AlertRepo.list_alerts 须支持 qc_record_id 数据层过滤"

    @pytest.mark.asyncio
    async def test_target_within_first_page_returned(self):
        """目标在首页内正常返回：字段完整（qc_record_id / alert_id / evidence）。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        wanted_id = str(record.id)
        rows = _build_rows(wanted_id=wanted_id, other_id=str(uuid4()), wanted_count=3, other_count=2)
        server = _server_with(RecordingAlertRepo(rows), record)

        items = await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )

        assert len(items) == 3
        target = next(item for item in items if item["alert_id"] == "target-0")
        assert target["qc_record_id"] == wanted_id
        assert target["alert_id"] == "target-0"
        assert "evidence" in target

    @pytest.mark.asyncio
    async def test_session_id_semantics_kept(self):
        """session_id 语义保持：params 的 session_id 原样传 repo；repo 层 None 时不过滤。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        rows = _build_rows(
            wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=2, other_count=1
        )
        repo = RecordingAlertRepo(rows)
        server = _server_with(repo, record)

        await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )
        assert repo.calls[0]["session_id"] == SESSION

        # repo 层：session_id=None 不过滤（含他 session 数据时仍全量返回）
        repo2 = RecordingAlertRepo(
            _build_rows(wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=1, other_count=1)
        )
        items, total = await repo2.list_alerts(page=1, page_size=100, session_id=None)
        assert len(items) == 2 and total == 2, "session_id 为空时不做会话过滤"

    @pytest.mark.asyncio
    async def test_evidence_masked(self):
        """evidence mask_text 脱敏保持：明文手机号不出现在出参。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        rows = _build_rows(
            wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=1, other_count=0
        )
        server = _server_with(RecordingAlertRepo(rows), record)

        items = await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )

        assert len(items) == 1
        assert MASKED_PHONE not in items[0]["evidence"]
        assert "****" in items[0]["evidence"]

    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        """无告警：返回 [] 而非 404/异常。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        server = _server_with(RecordingAlertRepo([]), record)

        items = await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )
        assert items == []

    @pytest.mark.asyncio
    async def test_same_record_over_100_returned_across_pages(self):
        """残余边界加固：同一 qc_record_id 告警超过 100 条时，分页遍历全部返回。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        wanted_id = str(record.id)
        rows = _build_rows(wanted_id=wanted_id, other_id=str(uuid4()), wanted_count=105, other_count=0)
        repo = RecordingAlertRepo(rows)
        server = _server_with(repo, record)

        items = await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )

        assert len(items) == 105, "同一记录 105 条告警须全部返回，不得在第 100 条截断"
        assert {"target-0", "target-104"} <= {item["alert_id"] for item in items}
        assert [call["page"] for call in repo.calls] == [1, 2], "按 total 自动翻页"

    @pytest.mark.asyncio
    async def test_call_form_kept_page1_size100(self):
        """首页形态保持：仍以 page=1, page_size=100 起取；全量性由数据层过滤 + 翻页保证。"""
        record = _record(session_id=SESSION, case_id=uuid4())
        rows = _build_rows(
            wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=1, other_count=1
        )
        repo = RecordingAlertRepo(rows)
        server = _server_with(repo, record)

        await server.call(
            "query_qc_alerts", {"case_id": str(record.case_id), "session_id": SESSION}
        )
        assert repo.calls[0]["page"] == 1
        assert repo.calls[0]["page_size"] == 100

    @pytest.mark.asyncio
    async def test_without_datalayer_filter_would_miss(self):
        """用例有效性反证：若不传 qc_record_id（旧形态），目标确实落在前 100 条之外。

        证明 test_target_beyond_100th_still_returned 的场景构造真实复现了
        旧缺陷的漏报条件，而非天然可查。
        """
        record = _record(session_id=SESSION, case_id=uuid4())
        rows = _build_rows(
            wanted_id=str(record.id), other_id=str(uuid4()), wanted_count=5, other_count=100
        )
        repo = RecordingAlertRepo(rows)

        stale_items, _total = await repo.list_alerts(page=1, page_size=100)
        assert all(item["qc_record_id"] != str(record.id) for item in stale_items), (
            "旧形态（无数据层过滤，只取前 100 条）下目标告警应漏报"
        )
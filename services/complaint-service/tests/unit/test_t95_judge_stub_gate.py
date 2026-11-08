"""T95：judge stub 生产门控 + run_graph 生产默认 True + V10 sim 自动审阅护栏。

spec complaint-service/llm-layer「Mock/Stub 路径的生产门控」：
- 生产（Mock 关闭）不执行 stub、不产出固定 verdict、不发布
  complaint.judged / case.risk.upgraded；
- 演示开关开启时 stub 按演示语义可用；
- 生产 run_graph 缺省默认 True 走真实图，stub 不可达；
- V10（待验证线索，随 4.1 同批评估）：生产 SIM_AUTO_REVIEW 自动审阅护栏。
"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from app.config import settings
from app.events.publisher import publisher as pub
from app.service.complaint_service import _mock_demo_mode, complaint_service
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException



async def _create(text: str, sid: str) -> dict:
    return await complaint_service.create_async(
        complaint_text=text, session_id=sid, case_id="c_t95", channel="phone"
    )


class TestT95StubGate:
    @pytest.mark.asyncio
    async def test_stub_rejected_when_both_mocks_off(self, monkeypatch):
        """生产（Mock 双关）：stub 拒绝执行，不产出固定 verdict、不发布事件。"""
        # 先在 Mock 模式下建单（conftest 缺省），再切生产开关——
        # USE_MOCK_MCP=false 时 create_async 走真实 PG 持久化，无 DB 会抛
        # ComplaintPersistError，与 stub 门控验收无关。
        rec = await _create("投诉内容", "s_t95_prod")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        pub.clear()

        with pytest.raises(BizException) as ei:
            await complaint_service.judge_async(rec["complaint_id"], run_graph=False)

        assert ei.value.code == ErrorCode.A5004
        topics = [e.topic for e in pub.list_events()]
        assert "complaint.judged" not in topics
        assert "case.risk.upgraded" not in topics

    @pytest.mark.asyncio
    async def test_stub_rejected_when_single_mock_on(self, monkeypatch):
        """单开关开启（另一关闭）仍视为生产：and 语义 fail-closed。"""
        rec = await _create("投诉内容", "s_t95_half")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        pub.clear()

        with pytest.raises(BizException):
            await complaint_service.judge_async(rec["complaint_id"], run_graph=False)

        assert "complaint.judged" not in [e.topic for e in pub.list_events()]

    @pytest.mark.asyncio
    async def test_stub_available_in_demo_mode(self):
        """演示（Mock 双开，conftest 缺省）：stub 按演示语义可用并发布事件。"""
        pub.clear()
        rec = await _create("投诉内容", "s_t95_demo")

        out = await complaint_service.judge_async(rec["complaint_id"], run_graph=False)

        from app.models.state import VerdictType

        assert out["verdict"] == VerdictType.agent_violation
        assert out["confidence"] == 0.92
        topics = [e.topic for e in pub.list_events()]
        assert "complaint.judged" in topics
        assert "case.risk.upgraded" in topics

    def test_mock_demo_mode_requires_both_switches(self, monkeypatch):
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
        assert _mock_demo_mode() is True
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        assert _mock_demo_mode() is False
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        assert _mock_demo_mode() is False


class TestT95RunGraphDefault:
    def test_production_default_true(self, monkeypatch):
        """生产（Mock 关闭）：run_graph 缺省默认 True 走真实图。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        from app.api.routes.complaints import judge_default_run_graph

        assert judge_default_run_graph() is True

    def test_demo_default_false(self, monkeypatch):
        """演示（Mock 双开）：保持 False stub 语义。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
        from app.api.routes.complaints import judge_default_run_graph

        assert judge_default_run_graph() is False

    def test_production_no_body_judge_never_reaches_stub(self, monkeypatch):
        """生产 + body 缺省：默认参数路由到真图分支（run_graph=True），
        stub 分支不可达——即使被错误调用也会被门控拒绝。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        from app.api.routes.complaints import judge_default_run_graph

        run_graph = judge_default_run_graph()
        assert run_graph is True
        # 纵深验证：生产下 stub 直调必然被门控拒绝（双保险）
        with pytest.raises(BizException):
            complaint_service._judge_stub("any-id")

    def test_empty_body_and_omitted_field_use_deployment_default(self, monkeypatch):
        """生产 + body `{}`（字段缺省 None）：路由解析为 True，MUST NOT 落 stub。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        from app.api.routes.complaints import resolve_run_graph
        from app.api.schemas.complaint import JudgeRequest

        assert JudgeRequest().run_graph is None
        assert resolve_run_graph(JudgeRequest()) is True
        assert resolve_run_graph(None) is True

    def test_explicit_value_wins_over_deployment_default(self, monkeypatch):
        """显式 run_graph 优先：生产显式 false 仍为 false（由 stub 门控拒绝）。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        from app.api.routes.complaints import resolve_run_graph
        from app.api.schemas.complaint import JudgeRequest

        assert resolve_run_graph(JudgeRequest(run_graph=False)) is False
        assert resolve_run_graph(JudgeRequest(run_graph=True)) is True

    def test_demo_empty_body_default_false(self, monkeypatch):
        """演示（Mock 双开）+ body `{}`：解析为 False，保持 stub 演示语义。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
        from app.api.routes.complaints import resolve_run_graph
        from app.api.schemas.complaint import JudgeRequest

        assert resolve_run_graph(JudgeRequest()) is False


class TestV10SimAutoReviewGate:
    @pytest.mark.asyncio
    async def test_sim_auto_review_disabled_in_production(self, monkeypatch):
        """V10：生产（Mock 关闭）+ SIM_AUTO_REVIEW=true → 忽略并 warning，
        不 resume 图（演示语义不得泄漏到生产人工复核环节）。"""
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
        monkeypatch.setattr(settings, "SIM_AUTO_REVIEW", True)

        with capture_logs() as logs:
            await complaint_service._maybe_sim_auto_review("nonexistent-id")

        assert any(
            e.get("event") == "sim_auto_review_disabled_in_production" for e in logs
        ), "生产应留 sim_auto_review_disabled warning"

    @pytest.mark.asyncio
    async def test_sim_auto_review_noop_when_switch_off(self, monkeypatch):
        """开关缺省 False：直接 no-op（既有语义不变）。"""
        monkeypatch.setattr(settings, "SIM_AUTO_REVIEW", False)

        with capture_logs() as logs:
            await complaint_service._maybe_sim_auto_review("nonexistent-id")

        assert not [
            e for e in logs if e.get("event") == "sim_auto_review_disabled_in_production"
        ]
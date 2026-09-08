"""检索链路 Trace 与 LangSmith 埋点单元测试。"""
import os

import pytest

from app.services.search_trace import SearchTraceCollector, configure_langsmith_env
from app.settings import settings


class TestSearchTraceCollector:
    def test_step_timing_and_finish(self):
        trace = SearchTraceCollector(trace_id="t-1", query="测试问题")
        with trace.step("embed", category="case"):
            pass
        with trace.step("recall", top_n=20):
            pass
        out = trace.finish(outputs={"results": 3})
        assert out["trace_id"] == "t-1"
        assert out["total_ms"] >= 0
        assert len(out["steps"]) == 2
        assert out["steps"][0]["name"] == "embed"
        assert out["steps"][1]["name"] == "recall"
        assert out["summary"]["results"] == 3

    def test_configure_langsmith_env_when_enabled(self, monkeypatch):
        monkeypatch.setattr(settings, "LANGSMITH_ENABLED", True)
        monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "test-key")
        monkeypatch.setattr(settings, "LANGSMITH_PROJECT", "test-proj")
        for k in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
            os.environ.pop(k, None)
        configure_langsmith_env()
        assert os.environ.get("LANGSMITH_TRACING") == "true"
        assert os.environ.get("LANGSMITH_API_KEY") == "test-key"
        assert os.environ.get("LANGSMITH_PROJECT") == "test-proj"

    @pytest.mark.asyncio
    async def test_langsmith_root_no_crash_when_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "LANGSMITH_ENABLED", False)
        monkeypatch.setattr(settings, "LANGSMITH_API_KEY", "")
        trace = SearchTraceCollector(trace_id="t-2", query="q")
        with trace.step("rerank"):
            pass
        out = trace.finish()
        assert out["steps"][0]["name"] == "rerank"

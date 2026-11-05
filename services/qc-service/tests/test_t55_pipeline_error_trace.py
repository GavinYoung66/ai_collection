"""T55 / 4.2：QC 主 pipeline 异常补 error 留痕 单测。

验收判据：
- pipeline 抛异常时 result.message 含异常类型（区别于裸 A2001 码，运维可
  区分"部分成功"与"崩溃"）；
- 日志记录 ``qc_pipeline_failed`` 且含 exc_info（完整栈留痕）；
- 返回形态保持 A2001 / PARTIAL / inserted=False（fail-safe 语义不变）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.services import offline_qc_service as oq_mod
from app.services.offline_qc_service import OfflineQcService
from common.errors.codes import ErrorCode


def _session() -> dict:
    return {
        "session_id": "s-1",
        "case_id": "c-1",
        "call_id": "call-1",
        "call_ended": True,
        "text_ready": True,
        "turns": [
            {"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 0}
        ],
    }


def _spy_logger(monkeypatch) -> Mock:
    spy = Mock()
    monkeypatch.setattr(oq_mod, "logger", spy)
    return spy


class TestPipelineErrorTrace:
    @pytest.mark.asyncio
    async def test_pipeline_exception_message_contains_type(self, monkeypatch):
        """pipeline 抛异常：result.message 含异常类型（不再是裸 A2001 码）。"""
        _spy_logger(monkeypatch)
        svc = OfflineQcService()
        monkeypatch.setattr(
            svc, "_pipeline", AsyncMock(side_effect=RuntimeError("boom: es down"))
        )

        result = await svc.run(_session())

        assert result.code == ErrorCode.A2001.value
        assert result.qc_status == "PARTIAL"
        assert result.inserted is False
        assert "RuntimeError" in result.message, "message 必须含异常类型摘要"
        assert "boom: es down" in result.message
        assert result.message != ErrorCode.A2001.value, (
            "裸错误码无法区分'部分成功'与'崩溃'"
        )

    @pytest.mark.asyncio
    async def test_pipeline_exception_logs_error_with_exc_info(self, monkeypatch):
        """pipeline 抛异常：logger.error('qc_pipeline_failed', exc_info=True)。"""
        spy = _spy_logger(monkeypatch)
        svc = OfflineQcService()
        monkeypatch.setattr(
            svc, "_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
        )

        await svc.run(_session())

        spy.error.assert_called_once()
        assert spy.error.call_args.args[0] == "qc_pipeline_failed"
        kwargs = spy.error.call_args.kwargs
        assert kwargs.get("exc_info") is True, "必须附完整栈（exc_info=True）"
        assert kwargs.get("session_id") == "s-1"

    @pytest.mark.asyncio
    async def test_exception_path_releases_inflight_gate(self, monkeypatch):
        """异常路径不破坏并发门控：inflight_gate 正常 release（finally 语义保留）。"""
        _spy_logger(monkeypatch)
        svc = OfflineQcService()
        gate_spy = Mock()
        gate_spy.acquire_wait = AsyncMock()
        gate_spy.release = AsyncMock()
        monkeypatch.setattr(svc, "inflight_gate", gate_spy)
        monkeypatch.setattr(
            svc, "_pipeline", AsyncMock(side_effect=RuntimeError("boom"))
        )

        await svc.run(_session())

        gate_spy.acquire_wait.assert_awaited_once()
        gate_spy.release.assert_awaited_once()
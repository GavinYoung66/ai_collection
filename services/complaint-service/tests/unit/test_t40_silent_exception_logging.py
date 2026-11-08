"""T40：静默异常治理（reconcile/stats_service 等）单测。

验收判据：``except Exception: pass`` 全仓零命中；捕获异常 MUST 记录
warning（业务允许忽略的为 debug）级日志并附异常栈（exc_info=True）。
除命名两处（reconcile.py / stats_service.py）外，本批一并治理了同族
静默吞异常点（callback/mode/llm provider/解析器/关停路径），并以
源码扫描守护防回潮。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from unittest.mock import Mock

from app.service import callback_service as cb_mod
from app.service import mode_service as mode_mod
from app.service import reconcile as reconcile_mod
from app.service import stats_service as stats_mod
from app.service.callback_service import CallbackService
from app.service.mode_service import ModeService
from app.service.reconcile import daily_reconcile
from app.service.stats_service import StatsService

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCAN_EXCLUDE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "openspec", "requirements_document", ".trae", "baseline",
}
# W6 强化：此前正则 `except Exception:\s*\n\s*pass` 无法匹配
# "except Exception:  # noqa 注释 + 换行 pass" 形态（行尾注释被 \s* 吞掉前
# 换行已断开匹配），13+ 处带 noqa 注释的静默 pass 成为守卫盲区。现允许
# except 行尾携带注释（#[^\n]*），其余语义不变。
# 批次修复 W5 强化：静默形态扩展覆盖 `continue`（asset_extractor 曾以
# "except Exception: continue" 绕过守卫）；return 固定值形态由人工评审覆盖，
# 不纳入正则（误报面过大）。
_SILENT_PASS = re.compile(r"except Exception:\s*(?:#[^\n]*)?\n\s*(?:pass|continue)\b")


def _spy_logger(monkeypatch, target_mod) -> Mock:
    spy = Mock()
    monkeypatch.setattr(target_mod, "logger", spy)
    return spy


class TestReconcileCompensateFailure:
    def test_failure_logs_warning_with_stack_and_continues(self, monkeypatch):
        """回调补偿抛异常：记 warning+栈，不中断对账，该 session 不入 compensated。"""
        spy = _spy_logger(monkeypatch, reconcile_mod)

        session_service = Mock()
        session_service.timeout_sessions.return_value = 2
        session_service.list_stale_draft_generated.return_value = ["s-1", "s-2"]

        callback_service = Mock()
        callback_service.send.side_effect = RuntimeError("cb down")

        result = daily_reconcile(session_service, callback_service, stale_hours=1.0)

        assert result["timed_out"] == 2
        assert result["stale_draft_generated"] == ["s-1", "s-2"]
        assert result["compensated"] == []
        assert spy.warning.call_count == 2
        for call in spy.warning.call_args_list:
            assert call.args[0] == "reconcile_callback_compensate_failed"
            assert call.kwargs.get("exc_info") is True

    def test_success_path_not_logging_warning(self, monkeypatch):
        """正常补偿路径不产生 warning。"""
        spy = _spy_logger(monkeypatch, reconcile_mod)
        session_service = Mock()
        session_service.timeout_sessions.return_value = 0
        session_service.list_stale_draft_generated.return_value = ["s-ok"]
        callback_service = Mock()
        callback_service.send.return_value = True

        result = daily_reconcile(session_service, callback_service, stale_hours=1.0)

        assert result["compensated"] == ["s-ok"]
        spy.warning.assert_not_called()


class TestStatsHotspotCacheCorrupted:
    def test_corrupted_cache_logs_warning_and_recomputes(self, monkeypatch):
        """热点缓存损坏：记 warning+栈，降级重算并覆写为合法 JSON。"""
        spy = _spy_logger(monkeypatch, stats_mod)

        class _BadCacheRedis:
            def __init__(self) -> None:
                self.store: dict[str, str] = {"csmp:hotspots:all:all": "{not-json"}

            def get(self, k):
                return self.store.get(k)

            def set(self, k, v, ex=None):
                self.store[k] = v
                return True

        redis = _BadCacheRedis()
        svc = StatsService(redis_client=redis)
        result = svc.get_stats()

        assert result["p95_processing_minutes"] > 0
        assert "generated_at" in result
        import json

        assert json.loads(redis.store["csmp:hotspots:all:all"]) == result
        spy.warning.assert_called_once()
        assert spy.warning.call_args.args[0] == "stats_hotspot_cache_corrupted"
        assert spy.warning.call_args.kwargs.get("exc_info") is True


class TestCallbackSendFailure:
    def test_each_attempt_logs_warning_then_dlq(self, monkeypatch):
        """3 次投递失败各记 warning+栈，耗尽后入 DLQ 并返回 False。"""
        spy = _spy_logger(monkeypatch, cb_mod)
        http = Mock()
        http.post.side_effect = RuntimeError("http down")
        svc = CallbackService(http_client=http, clock=lambda: 0.0)

        ok = svc.send("s-1", version=1, payload={"k": "v"})

        assert ok is False
        assert svc.dlq_size() == 1
        assert spy.warning.call_count == 3
        for i, call in enumerate(spy.warning.call_args_list, start=1):
            assert call.args[0] == "callback_send_attempt_failed"
            assert call.kwargs["attempt"] == i
            assert call.kwargs.get("exc_info") is True


class TestModeSwitchRollbackFailure:
    def test_rollback_failure_logs_warning_and_returns_a5014(self, monkeypatch):
        """切换失败 + 回滚再失败：记 warning+栈，仍返回 (False, A5014)。"""
        from app.utils.redis_keys import MODE_ACTIVE_KEY

        spy = _spy_logger(monkeypatch, mode_mod)

        class _BrokenSetRedis:
            def __init__(self) -> None:
                self._d = {MODE_ACTIVE_KEY: "multi_agent", "csmp:mode:gray_percent": "50"}

            def get(self, k):
                return self._d.get(k)

            def set(self, k, v, ex=None):
                raise RuntimeError("redis down")

        svc = ModeService(redis_client=_BrokenSetRedis())
        # T89：切换/回滚主体为 _advance（gray 越界校验已前移至 switch）；
        # T40 意图保持——推进失败 + 回滚再失败 MUST 记 warning 且返回 A5014
        ok, err = svc._advance("single_agent", None)

        assert ok is False
        assert err is not None
        spy.warning.assert_called_once()
        assert spy.warning.call_args.args[0] == "mode_switch_rollback_failed"
        assert spy.warning.call_args.kwargs.get("exc_info") is True


class TestLLMProviderObservabilityPaths:
    def test_emit_call_log_failure_logs_warning_not_raise(self, monkeypatch):
        """审计埋点失败：记 warning+栈，不影响 _record_audit 返回。"""
        from app.llm import provider as provider_mod

        spy = _spy_logger(monkeypatch, provider_mod)
        monkeypatch.setattr(
            "app.observability.call_log_sink.emit_call_log",
            Mock(side_effect=RuntimeError("sink down")),
        )
        svc = provider_mod.ComplaintLLMProvider(transport=object())

        svc._record_audit(
            prompt="p", trace_id="t1", session_id="s1",
            start=time.monotonic(), attempt=0, error="boom", response=None,
        )

        assert len(svc.call_log) == 1
        spy.warning.assert_called_once()
        assert spy.warning.call_args.args[0] == "llm_call_log_emit_failed"
        assert spy.warning.call_args.kwargs.get("exc_info") is True

    def test_cot_placeholder_fill_failure_logs_debug(self, monkeypatch):
        """cot 占位填充失败（frozen 对象）：记 debug+栈，不抛出。"""
        from app.llm import provider as provider_mod
        from app.llm.provider import ComplaintLLMProvider

        spy = _spy_logger(monkeypatch, provider_mod)

        class _FrozenCot:
            @property
            def cot_encrypted(self):
                return None

            def __setattr__(self, name, value):
                raise TypeError("frozen")

        ComplaintLLMProvider._fill_cot_encrypted(_FrozenCot())

        spy.debug.assert_called_once()
        assert spy.debug.call_args.args[0] == "llm_cot_placeholder_fill_failed"
        assert spy.debug.call_args.kwargs.get("exc_info") is True


class TestNoSilentExceptionPassGuard:
    def test_repo_wide_zero_silent_except_pass(self):
        """T40 验收：全仓（服务/公共代码与脚本）``except Exception: pass`` 零命中。"""
        offenders: list[str] = []
        for py in _REPO_ROOT.rglob("*.py"):
            if any(part in _SCAN_EXCLUDE_DIRS for part in py.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except OSError:
                continue
            if _SILENT_PASS.search(text):
                offenders.append(str(py))
        assert offenders == [], (
            f"发现静默吞异常（except Exception: pass）：{offenders}；"
            "应记录 warning/debug 日志并附 exc_info，或在代码处注明忽略理由"
        )

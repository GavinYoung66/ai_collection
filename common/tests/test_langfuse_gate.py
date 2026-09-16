"""Langfuse 链路追踪统一闸门（common.trace.langfuse_gate）单元测试。

覆盖三条约定：唯一开关判定、SDK 环境变量写入/清除、run 包裹的降级安全。
与 test_langsmith_gate.py 同构。
"""
import os
from types import SimpleNamespace

import pytest

from common.trace import langfuse_gate
from common.trace.langfuse_gate import (
    LangfuseRun,
    LangfuseSpanHandle,
    configure_langfuse_env,
    get_langfuse_client,
    langfuse_base_url,
    langfuse_enabled,
)

_ENV_KEYS = (
    "LANGFUSE_TRACING_ENABLED",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_HOST",
)


def _settings(**overrides):
    base = {
        "LANGFUSE_ENABLED": False,
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "LANGFUSE_BASE_URL": "http://localhost:3000",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeSpan:
    """最小可观测 span：记录 update / end / start_observation 调用。"""

    def __init__(self, record: list):
        self._record = record

    def update(self, **kwargs):
        self._record.append(("update", kwargs))

    def end(self):
        self._record.append(("end", {}))

    def start_observation(self, **kwargs):
        self._record.append(("start_observation", kwargs))
        return _FakeSpan(self._record)


class _FakeClient:
    def __init__(self, record: list):
        self._record = record

    def start_observation(self, **kwargs):
        self._record.append(("start_observation", kwargs))
        return _FakeSpan(self._record)


class TestLangfuseEnabled:
    def test_enabled_with_both_keys(self):
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        assert langfuse_enabled(cfg) is True

    def test_disabled_without_secret_key(self):
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk")
        assert langfuse_enabled(cfg) is False

    def test_enabled_without_public_key_is_off(self):
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_SECRET_KEY="sk")
        assert langfuse_enabled(cfg) is False

    def test_disabled_switch_is_off(self):
        cfg = _settings(LANGFUSE_ENABLED=False, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        assert langfuse_enabled(cfg) is False

    def test_blank_key_is_off(self):
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="  ", LANGFUSE_SECRET_KEY="sk")
        assert langfuse_enabled(cfg) is False

    def test_base_url_falls_back_to_local_default(self):
        assert langfuse_base_url(_settings(LANGFUSE_BASE_URL="")) == langfuse_gate._DEFAULT_BASE_URL
        assert langfuse_base_url(_settings(LANGFUSE_BASE_URL="http://lf:3001")) == "http://lf:3001"


class TestConfigureEnv:
    def test_disabled_clears_all_bypass_env(self, monkeypatch):
        for key in _ENV_KEYS:
            monkeypatch.setenv(key, "leftover")
        configure_langfuse_env(_settings(LANGFUSE_ENABLED=False))
        for key in _ENV_KEYS:
            assert key not in os.environ

    def test_enabled_writes_env(self, monkeypatch):
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        cfg = _settings(
            LANGFUSE_ENABLED=True,
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_BASE_URL="http://localhost:3000",
        )
        configure_langfuse_env(cfg)
        assert os.environ["LANGFUSE_TRACING_ENABLED"] == "true"
        assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test"
        assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-test"
        assert os.environ["LANGFUSE_BASE_URL"] == "http://localhost:3000"
        # 旧名同步，避免历史残留指向别的实例
        assert os.environ["LANGFUSE_HOST"] == "http://localhost:3000"

    def test_enabled_never_leaves_base_url_empty(self, monkeypatch):
        """base_url 留空时 SDK 会静默回落到 cloud，故必须写本地默认值。"""
        monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        configure_langfuse_env(cfg)
        assert os.environ["LANGFUSE_BASE_URL"] == langfuse_gate._DEFAULT_BASE_URL

    def test_disabled_after_enabled_clears(self, monkeypatch):
        cfg_on = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        configure_langfuse_env(cfg_on)
        configure_langfuse_env(_settings(LANGFUSE_ENABLED=False))
        assert "LANGFUSE_TRACING_ENABLED" not in os.environ
        assert "LANGFUSE_BASE_URL" not in os.environ


class TestGetLangfuseClient:
    def test_disabled_returns_none(self):
        assert get_langfuse_client(_settings(LANGFUSE_ENABLED=False)) is None

    def test_sdk_failure_degrades_to_none(self, monkeypatch):
        monkeypatch.setattr(langfuse_gate, "_client", None)
        monkeypatch.setattr(langfuse_gate, "_client_key", None)

        def _boom(*_a, **_k):
            raise RuntimeError("sdk down")

        monkeypatch.setattr(langfuse_gate, "configure_langfuse_env", _boom)
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        assert get_langfuse_client(cfg) is None


class TestLangfuseSpanHandle:
    def test_end_noop_without_span(self):
        handle = LangfuseSpanHandle(None)
        handle.end({"ok": True})
        handle.end(error="boom")

    def test_end_writes_output(self):
        record: list = []
        LangfuseSpanHandle(_FakeSpan(record)).end(outputs={"latency_ms": 3})
        assert record == [("update", {"output": {"latency_ms": 3}}), ("end", {})]

    def test_end_maps_error_to_level_and_status_message(self):
        record: list = []
        LangfuseSpanHandle(_FakeSpan(record)).end(error="boom")
        assert record == [("update", {"level": "ERROR", "status_message": "boom"}), ("end", {})]

    def test_span_exception_is_swallowed(self):
        class _Broken:
            def update(self, **_k):
                raise RuntimeError("update down")

            def end(self):
                raise RuntimeError("end down")

        LangfuseSpanHandle(_Broken()).end({"x": 1})


class TestLangfuseRun:
    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(
            langfuse_gate,
            "get_langfuse_client",
            lambda *a, **k: pytest.fail("关闭时不应建客户端"),
        )
        cfg = _settings(LANGFUSE_ENABLED=False)
        run = LangfuseRun("t", {"q": "x"}, settings=cfg)
        assert run.create_child("step") is None
        run.end({"ok": True})
        run.end(error="boom")

    def test_client_failure_degrades(self, monkeypatch):
        monkeypatch.setattr(langfuse_gate, "get_langfuse_client", lambda *a, **k: None)
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        run = LangfuseRun("t", {}, settings=cfg)
        assert run.create_child("step") is None
        run.end()

    def test_enabled_creates_root_and_child(self, monkeypatch):
        record: list = []
        monkeypatch.setattr(langfuse_gate, "get_langfuse_client", lambda *a, **k: _FakeClient(record))
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        run = LangfuseRun("root", {"query": "q"}, settings=cfg, metadata={"trace_id": "t-1"})
        assert record[-1][0] == "start_observation"
        assert record[-1][1]["as_type"] == "chain"
        assert record[-1][1]["metadata"] == {"trace_id": "t-1"}

        child = run.create_child("step", {"k": 1})
        assert record[-1][0] == "start_observation"
        assert record[-1][1]["as_type"] == "tool"
        child.end(outputs={"latency_ms": 1})
        assert ("end", {}) in record

        run.end(outputs={"total_ms": 2})
        assert record[-1] == ("end", {})

    def test_metadata_drops_empty_values(self, monkeypatch):
        record: list = []
        monkeypatch.setattr(langfuse_gate, "get_langfuse_client", lambda *a, **k: _FakeClient(record))
        cfg = _settings(LANGFUSE_ENABLED=True, LANGFUSE_PUBLIC_KEY="pk", LANGFUSE_SECRET_KEY="sk")
        LangfuseRun("root", {}, settings=cfg, metadata={"trace_id": "", "k": "v"})
        assert record[-1][1]["metadata"] == {"k": "v"}

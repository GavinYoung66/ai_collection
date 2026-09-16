"""LangSmith 链路追踪统一闸门（common.trace.langsmith_gate）单元测试。

覆盖三条约定：唯一开关判定、SDK 环境变量写入/清除、外链与 run 包裹的降级安全。
"""
import os
from types import SimpleNamespace

import pytest

from common.trace import langsmith_gate
from common.trace.langsmith_gate import (
    LangSmithRun,
    configure_langsmith_env,
    langsmith_enabled,
    langsmith_project,
    langsmith_url,
)

_ENV_KEYS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


def _settings(**overrides):
    base = {
        "LANGSMITH_ENABLED": False,
        "LANGSMITH_API_KEY": "",
        "LANGSMITH_PROJECT": "",
        "LANGSMITH_ENDPOINT": "https://api.smith.langchain.com",
        "LANGSMITH_TRACE_URL_TEMPLATE": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestLangsmithEnabled:
    def test_enabled_with_key(self):
        assert langsmith_enabled(_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k")) is True

    def test_enabled_without_key_is_off(self):
        assert langsmith_enabled(_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="")) is False

    def test_disabled_with_key_is_off(self):
        assert langsmith_enabled(_settings(LANGSMITH_ENABLED=False, LANGSMITH_API_KEY="k")) is False

    def test_blank_key_is_off(self):
        assert langsmith_enabled(_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="   ")) is False

    def test_project_fallback(self):
        assert langsmith_project(_settings(LANGSMITH_PROJECT="my-proj")) == "my-proj"
        assert langsmith_project(_settings(LANGSMITH_PROJECT=""), fallback="svc") == "svc"


class TestConfigureEnv:
    def test_disabled_clears_all_bypass_env(self, monkeypatch):
        for key in _ENV_KEYS:
            monkeypatch.setenv(key, "leftover")
        configure_langsmith_env(_settings(LANGSMITH_ENABLED=False, LANGSMITH_API_KEY="k"))
        for key in _ENV_KEYS:
            assert key not in os.environ

    def test_enabled_writes_env(self, monkeypatch):
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        cfg = _settings(
            LANGSMITH_ENABLED=True,
            LANGSMITH_API_KEY="test-key",
            LANGSMITH_PROJECT="test-proj",
        )
        configure_langsmith_env(cfg)
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
        assert os.environ["LANGSMITH_API_KEY"] == "test-key"
        assert os.environ["LANGSMITH_PROJECT"] == "test-proj"

    def test_enabled_project_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
        configure_langsmith_env(_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k"))
        assert os.environ["LANGSMITH_PROJECT"] == langsmith_gate._DEFAULT_PROJECT

    def test_disabled_after_enabled_clears(self, monkeypatch):
        configure_langsmith_env(_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k"))
        configure_langsmith_env(_settings(LANGSMITH_ENABLED=False, LANGSMITH_API_KEY="k"))
        assert "LANGSMITH_TRACING" not in os.environ
        assert "LANGSMITH_API_KEY" not in os.environ


class TestLangsmithUrl:
    _TMPL = "https://smith.langchain.com/o/x/projects/p/{project}/r/{run_id}"

    def test_none_when_disabled(self):
        cfg = _settings(LANGSMITH_ENABLED=False, LANGSMITH_API_KEY="k", LANGSMITH_TRACE_URL_TEMPLATE=self._TMPL)
        assert langsmith_url("run-1", cfg) is None

    def test_none_without_run_id(self):
        cfg = _settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k", LANGSMITH_TRACE_URL_TEMPLATE=self._TMPL)
        assert langsmith_url(None, cfg) is None
        assert langsmith_url("", cfg) is None

    def test_none_without_template(self):
        cfg = _settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k", LANGSMITH_TRACE_URL_TEMPLATE="")
        assert langsmith_url("run-1", cfg) is None

    def test_none_when_template_has_no_placeholder(self):
        cfg = _settings(
            LANGSMITH_ENABLED=True,
            LANGSMITH_API_KEY="k",
            LANGSMITH_TRACE_URL_TEMPLATE="https://smith.langchain.com/o/x",
        )
        assert langsmith_url("run-1", cfg) is None

    def test_formats_url(self):
        cfg = _settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k", LANGSMITH_TRACE_URL_TEMPLATE=self._TMPL)
        url = langsmith_url("run-1", cfg, fallback_project="rag-service")
        assert url == "https://smith.langchain.com/o/x/projects/p/rag-service/r/run-1"


class TestLangSmithRun:
    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(langsmith_gate, "configure_langsmith_env", lambda *a, **k: pytest.fail("不应写环境变量"))
        run = LangSmithRun("t", {}, settings=_settings(LANGSMITH_ENABLED=False, LANGSMITH_API_KEY="k"))
        assert run.run_id is None
        run.end({"ok": True})
        run.end(error="boom")

    def test_sdk_failure_degrades(self, monkeypatch):
        def _boom(*_a, **_k):
            raise RuntimeError("sdk down")

        monkeypatch.setattr(langsmith_gate, "configure_langsmith_env", _boom)
        run = LangSmithRun("t", {}, settings=_settings(LANGSMITH_ENABLED=True, LANGSMITH_API_KEY="k"))
        assert run.run_id is None
        run.end()

    def test_end_without_root_is_noop(self):
        run = LangSmithRun.__new__(LangSmithRun)
        run._root = None
        run.run_id = None
        run.end({"x": 1})

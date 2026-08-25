"""敏感词 seed 初始化 PUBLISH 单元测试（B2 / D3）。

覆盖：
- count<50→=50 时 seed 后发布版本事件
- 已 ≥50 词时跳过不发布（幂等）
- PUBLISH payload 三字段完整（version/trace_id/ts）
- 发布失败仅 warning 不阻断
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.main import _ensure_sensitive_words_baseline


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_session(before_count, after_count, n_inserts):
    """构造 mock AsyncSession：execute 按序返回 count → INSERT×n → recount。"""
    session = MagicMock()
    session.commit = AsyncMock()

    def _result(words):
        r = MagicMock()
        r.scalars.return_value.all.return_value = words
        return r

    results = [_result([f"word_{i}" for i in range(before_count)])]
    results += [_result([]) for _ in range(n_inserts)]
    results += [_result([f"word_{i}" for i in range(after_count)])]
    session.execute = AsyncMock(side_effect=results)
    return session


def _write_dict(tmp_path, n):
    p = tmp_path / "dict.txt"
    p.write_text("\n".join(f"敏感词_{i}" for i in range(n)), encoding="utf-8")
    return p


def _setup(monkeypatch, session, redis, dict_path):
    monkeypatch.setattr(
        "common.db.session.async_session_factory",
        MagicMock(return_value=_FakeSessionCtx(session)),
    )
    monkeypatch.setattr("app.api.deps.get_redis", AsyncMock(return_value=redis))
    monkeypatch.setattr("common.trace.middleware.get_trace_id", lambda: "trace-test")
    monkeypatch.setattr("app.services.sensitive_words_sync._DICT_FILE", dict_path)


def _make_redis(publish_fail=False):
    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value="1")
    if publish_fail:
        redis.publish = AsyncMock(side_effect=ConnectionError("pubsub down"))
    else:
        redis.publish = AsyncMock()
    return redis


class TestSeedPublish:
    @pytest.mark.asyncio
    async def test_seed_publishes_when_count_reaches_threshold(self, monkeypatch, tmp_path):
        """count<50→=50 时 seed 后发布版本事件。"""
        dict_path = _write_dict(tmp_path, 50)
        session = _make_session(before_count=40, after_count=50, n_inserts=50)
        redis = _make_redis()
        _setup(monkeypatch, session, redis, dict_path)

        await _ensure_sensitive_words_baseline()

        redis.publish.assert_called_once()
        channel, payload = redis.publish.call_args[0]
        assert channel == "kb.sensitive_updated"
        data = json.loads(payload)
        assert data["version"] == 1
        assert data["trace_id"] == "trace-test"
        assert isinstance(data["ts"], int)

    @pytest.mark.asyncio
    async def test_seed_skip_when_already_50(self, monkeypatch, tmp_path):
        """已 ≥50 词时不发布（幂等）。"""
        dict_path = _write_dict(tmp_path, 50)
        session = _make_session(before_count=50, after_count=50, n_inserts=0)
        redis = _make_redis()
        _setup(monkeypatch, session, redis, dict_path)

        await _ensure_sensitive_words_baseline()

        redis.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_publish_failure_warning_only(self, monkeypatch, tmp_path):
        """发布失败仅 warning，不阻断启动自检。"""
        dict_path = _write_dict(tmp_path, 50)
        session = _make_session(before_count=40, after_count=50, n_inserts=50)
        redis = _make_redis(publish_fail=True)
        _setup(monkeypatch, session, redis, dict_path)

        # 不抛异常即通过（publish_version 内部 catch 并 warning）
        await _ensure_sensitive_words_baseline()

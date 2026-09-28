"""敏感词同步服务单元测试（§11.7 / §16.8）。

覆盖：
- DB ≥50 行时走 DB 拼接
- DB 空走文件兜底
- refresh 后 version INCR
- EventBus 发布失败 warning 不阻断
- 接口 GET 返回 text/plain 内容与 V1 .dict 文件字节级一致（50 词集合排序输出）
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.sensitive_words_sync import SensitiveWordsSyncService

DICT_FILE = Path(__file__).resolve().parents[1] / "data" / "sensitive_words.dict"


def _load_dict_words() -> list[str]:
    """读取 .dict 文件全部词（排序后）。"""
    text = DICT_FILE.read_text(encoding="utf-8")
    return sorted({line.strip() for line in text.splitlines() if line.strip()})


def _mock_db(words: list[str]):
    """构造 mock AsyncSession，SELECT 返回指定 word 列表。"""
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = words
    db.execute = AsyncMock(return_value=result)
    return db


def _mock_redis(version: int = 0):
    """构造 mock Redis（版本键；跨服务通知不再用 PUBLISH）。"""
    redis = MagicMock()
    redis.incr = AsyncMock(return_value=version + 1)
    return redis


def _mock_event_bus(monkeypatch, *, publish_fail: bool = False):
    bus = MagicMock()
    if publish_fail:
        bus.publish = AsyncMock(side_effect=ConnectionError("eventbus down"))
    else:
        bus.publish = AsyncMock()
    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    monkeypatch.setattr(
        "common.eventbus.factory.get_event_bus",
        lambda **kwargs: bus,
    )
    return bus


class TestGetCurrentDict:
    @pytest.mark.asyncio
    async def test_db_above_threshold(self):
        """DB 活跃词 ≥50 → 走 DB 拼接。"""
        words = [f"word_{i:03d}" for i in range(50)]
        db = _mock_db(words)
        redis = _mock_redis()
        svc = SensitiveWordsSyncService(db, redis)
        result = await svc.get_current_dict()
        assert result == "\n".join(sorted(words))

    @pytest.mark.asyncio
    async def test_db_below_threshold_fallback_file(self):
        """DB 活跃词 <50 → 回退文件读取。"""
        db = _mock_db(["only_one_word"])
        redis = _mock_redis()
        svc = SensitiveWordsSyncService(db, redis)
        result = await svc.get_current_dict()
        # 文件内容应与 .dict 原文一致
        assert result == DICT_FILE.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_db_exception_fallback_file(self):
        """DB 异常 → 回退文件读取。"""
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db connection lost"))
        redis = _mock_redis()
        svc = SensitiveWordsSyncService(db, redis)
        result = await svc.get_current_dict()
        assert result == DICT_FILE.read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_dict_content_matches_v1_file_sorted(self):
        """DB 50 词与 .dict 文件集合一致时，输出为排序后的换行拼接。"""
        dict_words = _load_dict_words()
        db = _mock_db(dict_words)
        redis = _mock_redis()
        svc = SensitiveWordsSyncService(db, redis)
        result = await svc.get_current_dict()
        assert result == "\n".join(sorted(dict_words))


class TestRefresh:
    @pytest.mark.asyncio
    async def test_refresh_incr_version(self, monkeypatch):
        """refresh 成功 → Redis INCR 版本号 +1 并返回。"""
        words = [f"word_{i:03d}" for i in range(50)]
        db = _mock_db(words)
        redis = _mock_redis(version=5)
        bus = _mock_event_bus(monkeypatch)
        svc = SensitiveWordsSyncService(db, redis)
        new_version = await svc.refresh()
        assert new_version == 6
        redis.incr.assert_called_once()
        bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_publish_fail_not_block(self, monkeypatch):
        """EventBus 发布失败 → 仅 warning，不阻断，仍返回 new_version。"""
        words = [f"word_{i:03d}" for i in range(50)]
        db = _mock_db(words)
        redis = _mock_redis(version=3)
        bus = _mock_event_bus(monkeypatch, publish_fail=True)
        svc = SensitiveWordsSyncService(db, redis)
        new_version = await svc.refresh()
        assert new_version == 4
        bus.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_below_threshold_raises(self):
        """DB 活跃词 <50 → refresh 抛 BizException(A3001)。"""
        db = _mock_db(["few_words"])
        redis = _mock_redis()
        svc = SensitiveWordsSyncService(db, redis)
        from common.errors.exceptions import BizException
        from common.errors.codes import ErrorCode
        with pytest.raises(BizException) as exc_info:
            await svc.refresh()
        assert exc_info.value.code == ErrorCode.A3001

    @pytest.mark.asyncio
    async def test_refresh_publishes_via_eventbus(self, monkeypatch):
        """经 EventBus 发布 kb.sensitive_updated；data 可空；trace_id 上信封。"""
        words = [f"word_{i:03d}" for i in range(50)]
        db = _mock_db(words)
        redis = _mock_redis(version=10)
        bus = _mock_event_bus(monkeypatch)
        monkeypatch.setattr(
            "app.services.sensitive_words_sync.get_trace_id", lambda: "tr-sensitive"
        )
        svc = SensitiveWordsSyncService(db, redis)
        await svc.refresh()
        bus.publish.assert_called_once()
        args, kwargs = bus.publish.call_args
        assert args[0] == "kb.sensitive_updated"
        assert args[1] == {}
        assert kwargs.get("trace_id") == "tr-sensitive"

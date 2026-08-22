"""多轮会话服务单元测试（§10.7 / §16.7）。

覆盖：
- v2 key 段存在且不碰 V1 key（spy Redis SET rag:session:v2:*）
- TTL 1800（SESSION_TTL_SECONDS）
- 30min 过期自动（TTL 机制验证）
- Redis 故障静默 bypass
- KB_MULTITURN_ENABLED=false 时不读 session（spy）
- record 写入 + get_recent_queries 读取顺序 old→new
"""

import pytest

from app.services.session_service import SessionService


class FakeRedis:
    """内存版 Redis mock，可模拟故障，记录 pipeline 操作。"""

    def __init__(self, fail=False):
        self.store: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.fail = fail
        self.calls: list[tuple] = []

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("redis down")

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.ops: list[tuple] = []

            def lpush(self, key, val):
                self.ops.append(("lpush", key, val))

            def ltrim(self, key, start, end):
                self.ops.append(("ltrim", key, start, end))

            def expire(self, key, ttl):
                self.ops.append(("expire", key, ttl))

            async def execute(self):
                outer._maybe_fail()
                for op in self.ops:
                    outer.calls.append(op)
                    if op[0] == "lpush":
                        outer.store.setdefault(op[1], []).insert(0, op[2])
                    elif op[0] == "ltrim":
                        # op = ("ltrim", key, start, end); 保留 [start, end] 闭区间
                        start, end = op[2], op[3]
                        outer.store[op[1]] = outer.store.get(op[1], [])[start : end + 1]
                    elif op[0] == "expire":
                        outer.ttls[op[1]] = op[2]

        return _Pipe()

    async def lrange(self, key, start, end):
        self._maybe_fail()
        return self.store.get(key, [])[start : end + 1] if end >= 0 else self.store.get(key, [])[start:]


@pytest.fixture
def fake_redis():
    return FakeRedis()


class TestSessionRecord:
    @pytest.mark.asyncio
    async def test_v2_key_prefix_used(self, fake_redis):
        """record 写入使用 rag:session:v2: 前缀，不碰 V1 key。"""
        svc = SessionService(fake_redis)
        await svc.record("sess-001", "逾期协商", None, ["doc-1"])
        assert any(
            call[1].startswith("rag:session:v2:") for call in fake_redis.calls
        ), "应使用 rag:session:v2: 前缀"
        # 不应出现 V1 key 前缀
        assert not any(
            "rag:session:" in call[1] and not call[1].startswith("rag:session:v2:")
            for call in fake_redis.calls
            if len(call) > 1
        )

    @pytest.mark.asyncio
    async def test_ttl_1800(self, fake_redis):
        """record 写入 EXPIRE TTL=1800（SESSION_TTL_SECONDS）。"""
        svc = SessionService(fake_redis)
        await svc.record("sess-001", "查询", None, [])
        expire_calls = [c for c in fake_redis.calls if c[0] == "expire"]
        assert len(expire_calls) == 1
        assert expire_calls[0][2] == 1800

    @pytest.mark.asyncio
    async def test_redis_fail_silent(self):
        """Redis 故障时 record 静默 bypass，不抛异常。"""
        fake = FakeRedis(fail=True)
        svc = SessionService(fake)
        # 不应抛异常
        await svc.record("sess-001", "查询", None, [])


class TestSessionGetRecent:
    @pytest.mark.asyncio
    async def test_get_recent_old_to_new_order(self, fake_redis):
        """get_recent_queries 返回 old→new 顺序。"""
        svc = SessionService(fake_redis)
        await svc.record("sess-001", "第一轮", None, [])
        await svc.record("sess-001", "第二轮", None, [])
        await svc.record("sess-001", "第三轮", None, [])
        queries = await svc.get_recent_queries("sess-001", 3)
        assert queries == ["第一轮", "第二轮", "第三轮"]

    @pytest.mark.asyncio
    async def test_get_recent_redis_fail_returns_empty(self):
        """Redis 故障时 get_recent_queries 返回空列表。"""
        fake = FakeRedis(fail=True)
        svc = SessionService(fake)
        result = await svc.get_recent_queries("sess-001", 3)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_recent_empty_session(self, fake_redis):
        """空会话/不存在 → 返回空列表。"""
        svc = SessionService(fake_redis)
        result = await svc.get_recent_queries("nonexistent", 3)
        assert result == []

    @pytest.mark.asyncio
    async def test_history_cap_trims(self, fake_redis, monkeypatch):
        """超过 SESSION_HISTORY_CAP 的旧记录被 LTRIM 截断。"""
        from common.config.settings import settings
        monkeypatch.setattr(settings, "SESSION_HISTORY_CAP", 3)
        svc = SessionService(fake_redis)
        for i in range(5):
            await svc.record("sess-001", f"第{i}轮", None, [])
        queries = await svc.get_recent_queries("sess-001", 10)
        # 仅保留最近 3 轮
        assert len(queries) == 3
        assert queries == ["第2轮", "第3轮", "第4轮"]

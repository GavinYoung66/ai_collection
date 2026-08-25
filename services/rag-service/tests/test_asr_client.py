"""ASR 客户端熔断探测容错测试（rag-service-final-fix 5.5/5.6）。"""
import pytest
from redis.exceptions import RedisError

from app.services.asr_client import ASRClient


class TestDegradedProbeRedisError:
    @pytest.mark.asyncio
    async def test_degraded_probe_redis_error_no_propagate(self):
        """Redis 探测抛 RedisError → 不传播，按未熔断处理返回 False。"""
        class BoomRedis:
            async def exists(self, key):
                raise RedisError("connection refused")

        client = ASRClient(redis=BoomRedis())
        assert await client._is_degraded() is False

    @pytest.mark.asyncio
    async def test_degraded_probe_oserror_no_propagate(self):
        """Redis 探测抛 OSError → 不传播，按未熔断处理返回 False。"""
        class BoomRedis:
            async def exists(self, key):
                raise OSError("socket closed")

        client = ASRClient(redis=BoomRedis())
        assert await client._is_degraded() is False

    @pytest.mark.asyncio
    async def test_degraded_probe_normal_states(self):
        """正常探测：key 存在 → True；不存在 → False。"""
        class DegradedRedis:
            async def exists(self, key):
                return 1

        class NormalRedis:
            async def exists(self, key):
                return 0

        assert await ASRClient(redis=DegradedRedis())._is_degraded() is True
        assert await ASRClient(redis=NormalRedis())._is_degraded() is False

    @pytest.mark.asyncio
    async def test_degraded_probe_no_redis_false(self):
        """未配置 Redis → 恒为未熔断。"""
        assert await ASRClient(redis=None)._is_degraded() is False

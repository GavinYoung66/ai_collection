"""ASR 客户端：熔断已迁到 DashScope Provider，本层不再探测/写入废弃全局键。"""
import pytest

from app.services.asr_client import ASRClient


class RecordingRedis:
    def __init__(self):
        self.ops: list[tuple] = []

    async def exists(self, key):
        self.ops.append(("exists", key))
        return 0

    async def setex(self, key, ttl, value):
        self.ops.append(("setex", key))


class TestAsrClientNoLegacyDegradedKey:
    @pytest.mark.asyncio
    async def test_transcribe_does_not_touch_redis(self, monkeypatch):
        """转写成功不探测、不 SET 废弃全局键。"""
        redis = RecordingRedis()
        client = ASRClient(redis=redis)

        class MockProvider:
            async def transcribe(self, audio_bytes, fmt):
                return "ok"

        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create", lambda: MockProvider()
        )
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == "ok"
        assert redis.ops == []
        assert not hasattr(client, "_is_degraded")

    @pytest.mark.asyncio
    async def test_provider_failure_does_not_setex(self, monkeypatch):
        """Provider 失败向上抛，客户端不写 Redis。"""
        redis = RecordingRedis()
        client = ASRClient(redis=redis)

        class BoomProvider:
            async def transcribe(self, audio_bytes, fmt):
                raise RuntimeError("ASR service is in circuit-break state")

        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create", lambda: BoomProvider()
        )
        with pytest.raises(RuntimeError, match="circuit-break"):
            await client.transcribe(b"fake", "test.mp3")
        assert redis.ops == []

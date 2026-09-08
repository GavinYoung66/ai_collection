"""rag-service 配置项单元测试（原 common KB_* 已迁至 RagSettings）。"""
from app.rag_settings import RagSettings
from app.settings import platform_settings, rag_settings, settings


class TestSettingsProxyExports:
    def test_explicit_exports_and_proxy_route(self):
        assert settings.KB_CHUNK_SIZE == rag_settings.KB_CHUNK_SIZE
        assert settings.PG_HOST == platform_settings.PG_HOST
        assert "KB_CHUNK_SIZE" in dir(settings)


class TestRagFileSizeParseTimeout:
    def test_rag_file_size_parse_timeout_defaults(self):
        """KB_MAX_FILE_SIZE 默认 20MB、KB_PARSE_TIMEOUT 默认 30s（对齐 DD-03 §8）。"""
        s = RagSettings()
        assert s.KB_MAX_FILE_SIZE == 20 * 1024 * 1024
        assert s.KB_PARSE_TIMEOUT == 30.0

    def test_rag_file_size_env_override(self, monkeypatch):
        monkeypatch.setenv("KB_MAX_FILE_SIZE", str(10 * 1024 * 1024))
        s = RagSettings()
        assert s.KB_MAX_FILE_SIZE == 10 * 1024 * 1024

    def test_rag_parse_timeout_env_override(self, monkeypatch):
        monkeypatch.setenv("KB_PARSE_TIMEOUT", "60")
        s = RagSettings()
        assert s.KB_PARSE_TIMEOUT == 60.0

    def test_kb_ingest_job_timeout_default(self):
        s = RagSettings()
        assert s.KB_INGEST_JOB_TIMEOUT == 1800.0

    def test_kb_doc_chunk_keep_default_and_override(self, monkeypatch):
        """KB_DOC_CHUNK_KEEP 默认 5，可通过环境变量覆盖。"""
        s = RagSettings()
        assert s.KB_DOC_CHUNK_KEEP == 5
        monkeypatch.setenv("KB_DOC_CHUNK_KEEP", "3")
        s = RagSettings()
        assert s.KB_DOC_CHUNK_KEEP == 3


class TestHealthDegradedMode:
    def test_health_degraded_mode_default_false(self):
        """KB_HEALTH_DEGRADED_MODE 默认 false（严格探活：任一依赖失败 → unhealthy）。"""
        s = RagSettings()
        assert s.KB_HEALTH_DEGRADED_MODE is False

    def test_health_degraded_mode_env_override(self, monkeypatch):
        monkeypatch.setenv("KB_HEALTH_DEGRADED_MODE", "true")
        s = RagSettings()
        assert s.KB_HEALTH_DEGRADED_MODE is True

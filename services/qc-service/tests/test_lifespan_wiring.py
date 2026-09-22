"""启动接线：set_default_service("qc")，熔断/限流 key 使用 qc 前缀。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import qc_settings
from app.deps import reset_redis as reset_qc_redis
from app.main import app
from common.llm.reliability import resolve_service_name, set_default_service
from common.resilience import hooks
from common.resilience._redis import get_redis as common_get_redis
from common.resilience._redis import reset_redis as reset_common_redis
from common.resilience.breaker import make_llm_breaker_key, make_mcp_breaker_key


def test_lifespan_sets_qc_prefix_and_unwires_hooks():
    try:
        with TestClient(app):
            assert hooks.get_publisher() is not None
            assert resolve_service_name() == "qc"
            assert make_llm_breaker_key("qc", "qwen-plus") == "qc:llm_degraded:qwen-plus"
            assert make_mcp_breaker_key("qc", "rag_kb") == "qc:mcp_degraded:rag_kb"
            assert make_llm_breaker_key(resolve_service_name(), "qwen-plus").startswith("qc:")
            redis = common_get_redis()
            assert redis.connection_pool.connection_kwargs.get("db") == qc_settings.QC_REDIS_DB
        assert hooks.get_publisher() is None
    finally:
        hooks.set_publisher(None)
        set_default_service(None)
        reset_common_redis()
        reset_qc_redis()

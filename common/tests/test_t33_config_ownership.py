"""T33：配置职责治理单测。

验收口径：
- common 平台 Settings 只保留平台级参数（基础设施/密钥/公共超时/LLM 可靠性）；
- 业务参数下沉到所属服务配置（外呼 → call-service ``CallSettings``）；
- 各服务配置统一继承 common Settings，不再复制 ``BaseSettings`` 骨架；
- 合规模块（时段/并发）由调用方显式注入配置，common 不读业务全局配置。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from common.config.settings import Settings, settings as platform_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_config(module_name: str, relative_path: str):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def call_settings_cls():
    module = _load_config("t33_call_config", "services/call-service/app/config.py")
    return module.CallSettings


@pytest.mark.parametrize(
    "module_name,relative_path,attr",
    [
        ("t33_qc_config", "services/qc-service/app/core/config.py", "QcSettings"),
        ("t33_dp_config", "services/data-platform/app/core/config.py", "DataPlatformSettings"),
        ("t33_rag_config", "services/rag-service/app/rag_settings.py", "RagSettings"),
        ("t33_gateway_config", "services/mcp-gateway/app/config.py", "GatewaySettings"),
        ("t33_complaint_config", "services/complaint-service/app/config.py", "ComplaintSettings"),
    ],
)
def test_service_settings_inherit_platform(module_name, relative_path, attr):
    """服务级配置统一继承 common Settings，共享唯一平台事实源。"""
    module = _load_config(module_name, relative_path)
    cls = getattr(module, attr)
    assert issubclass(cls, Settings), f"{attr} 必须继承 common.config.settings.Settings"


def test_call_settings_owns_business_params(call_settings_cls):
    """外呼/会话业务参数归属 call-service，默认值与历史契约一致。"""
    assert issubclass(call_settings_cls, Settings)
    s = call_settings_cls()
    assert s.CALL_TIMEZONE == "Asia/Shanghai"
    assert s.CALL_ALLOWED_HOURS == "08:00-21:00"
    assert s.CALL_MAX_CONCURRENCY == 50
    assert s.MAX_DIALOG_TURNS == 15
    assert s.LLM_CONFIDENCE_THRESHOLD == 0.7


def test_business_params_absent_from_platform_settings():
    """common 不得再声明业务参数（防职责回流）。"""
    business = {
        "CALL_TIMEZONE",
        "CALL_ALLOWED_HOURS",
        "CALL_DAILY_LIMIT_PER_USER",
        "CALL_RETRY_INTERVAL_MIN",
        "CALL_MAX_RETRY",
        "CALL_MAX_CONCURRENCY",
        "MAX_DIALOG_TURNS",
        "LLM_CONFIDENCE_THRESHOLD",
    }
    assert business.isdisjoint(Settings.model_fields.keys())


def test_service_settings_do_not_redeclare_platform_fields(call_settings_cls):
    """服务配置只声明自身业务字段，不复制平台字段（避免第二事实源）。"""
    platform_fields = set(Settings.model_fields.keys())
    for cls in (call_settings_cls,):
        own_fields = set(cls.__annotations__)
        assert own_fields.isdisjoint(platform_fields)


def test_call_settings_env_override(monkeypatch, call_settings_cls):
    """业务参数仍从同名环境变量读取（.env 契约不变）。"""
    monkeypatch.setenv("CALL_MAX_CONCURRENCY", "7")
    s = call_settings_cls()
    assert s.CALL_MAX_CONCURRENCY == 7


def test_platform_settings_keeps_llm_reliability():
    """LLM 熔断阈值是跨服务平台可靠性参数，不应随业务参数下沉。"""
    assert "LLM_CIRCUIT_BREAKER_THRESHOLD" in Settings.model_fields
    assert "LLM_CIRCUIT_BREAKER_RECOVERY" in Settings.model_fields
    assert platform_settings.LLM_CIRCUIT_BREAKER_THRESHOLD == 3


def test_time_guard_requires_injected_config():
    """common 合规模块不再隐式读取全局业务配置。"""
    from common.utils.time_guard import check_call_time

    with pytest.raises(TypeError):
        check_call_time()  # type: ignore[call-arg]


def test_call_semaphore_requires_injected_limit():
    """并发上限由调用方注入，非法值快速失败。"""
    from common.utils.semaphore import CallSemaphore

    with pytest.raises(TypeError):
        CallSemaphore()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        CallSemaphore(0)

"""common/security/tests 独立 fixtures（不继承 common/tests/conftest.py）。"""
import pytest

from common.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个测试后恢复 settings 原始值，禁止读真实 .env 密钥。"""
    original = {k: v for k, v in settings.__dict__.items() if not k.startswith("_")}
    yield
    for k, v in original.items():
        if not k.startswith("_"):
            setattr(settings, k, v)


@pytest.fixture()
def jwt_secret(monkeypatch):
    key = "test_jwt_secret_key"
    monkeypatch.setattr(settings, "JWT_SECRET", key)
    return key


@pytest.fixture()
def issuer(monkeypatch):
    val = "https://auth.example.com"
    monkeypatch.setattr(settings, "JWT_ISSUER", val)
    return val


@pytest.fixture()
def audience(monkeypatch):
    val = "api"
    monkeypatch.setattr(settings, "JWT_AUDIENCE", val)
    return val


@pytest.fixture()
def api_keys(monkeypatch):
    val = "ak-abc123,ak-def456"
    monkeypatch.setattr(settings, "API_KEYS", val)
    return val


@pytest.fixture()
def api_key_role(monkeypatch):
    val = "admin"
    monkeypatch.setattr(settings, "API_KEY_ROLE", val)
    return val

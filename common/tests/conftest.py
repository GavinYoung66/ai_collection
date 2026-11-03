"""pytest 共享 fixtures。"""
from secrets import token_bytes

import pytest

from common.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个测试后恢复 settings 原始值。"""
    original = {k: v for k, v in settings.__dict__.items() if not k.startswith("_")}
    yield
    for k, v in original.items():
        if not k.startswith("_"):
            setattr(settings, k, v)


@pytest.fixture()
def aes_key(monkeypatch):
    """提供有效 32 字节 AES 密钥，直接 patch settings。"""
    key = token_bytes(32).hex()
    monkeypatch.setattr(settings, "AES_KEY", key)
    return key


@pytest.fixture()
def jwt_secret(monkeypatch):
    """提供 JWT 密钥。"""
    # ≥32 字节：满足 RFC 7518 §3.2 对 HS256 密钥长度的建议，避免 PyJWT 告警
    key = "test_jwt_secret_key_0123456789abcdef"
    monkeypatch.setattr(settings, "JWT_SECRET", key)
    return key

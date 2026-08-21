"""pytest 共享 fixtures。"""
from secrets import token_bytes

import pytest

from common.config.settings import settings


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个测试后恢复 settings 原始值。"""
    import copy
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
    monkeypatch.setattr(settings, "JWT_SECRET", "test_jwt_secret_key")
    return "test_jwt_secret_key"

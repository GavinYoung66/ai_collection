"""T48 / 2.1：rag-service lifespan 密钥 fail-fast 校验单测。

验收判据：
- 占位密钥（change_me）启动即 RuntimeError（fail-fast），startup 不执行；
- 合法密钥正常通过校验并进入 startup（与 qc-service 等其余 4 服务对齐）；
- AES_KEY 非法格式同样拒启（复用 validate_required_secrets 单源校验）。

灰度说明（部署期行为收紧）：以占位密钥运行的部署实例升级后拒启，
运维须先注入合法密钥；CI 以 `ci_jwt_secret_for_tests_only` 显式注入不受影响。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import main
from common.config.settings import settings as platform_settings


class TestLifespanSecretFailFast:
    @pytest.mark.asyncio
    async def test_placeholder_jwt_secret_rejected_before_startup(
        self, monkeypatch
    ):
        """JWT_SECRET 为占位值：进入 lifespan 即抛 RuntimeError，startup 不执行。"""
        monkeypatch.setattr(platform_settings, "JWT_SECRET", "change_me")
        startup_mock = AsyncMock()
        monkeypatch.setattr(main, "startup", startup_mock)

        with pytest.raises(RuntimeError, match="占位值"):
            async with main.lifespan(main.app):
                pass

        startup_mock.assert_not_awaited(), "fail-fast 应在 startup 之前生效"

    @pytest.mark.asyncio
    async def test_invalid_aes_key_rejected_before_startup(self, monkeypatch):
        """AES_KEY 非法 hex（非 64 位）：同样拒启（复用平台单源校验，T41 口径）。"""
        monkeypatch.setattr(
            platform_settings, "JWT_SECRET", "ci_jwt_secret_for_tests_only"
        )
        monkeypatch.setattr(platform_settings, "AES_KEY", "not_hex_key")
        startup_mock = AsyncMock()
        monkeypatch.setattr(main, "startup", startup_mock)

        with pytest.raises(RuntimeError):
            async with main.lifespan(main.app):
                pass

        startup_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_secrets_start_normally(self, monkeypatch):
        """合法密钥：校验通过，startup 正常执行（与 4 服务对齐的接入形态）。"""
        monkeypatch.setattr(
            platform_settings, "JWT_SECRET", "ci_jwt_secret_for_tests_only"
        )
        monkeypatch.setattr(platform_settings, "AES_KEY", "a" * 64)  # 合法 32B hex
        startup_mock = AsyncMock()
        monkeypatch.setattr(main, "startup", startup_mock)

        async with main.lifespan(main.app):
            pass

        startup_mock.assert_awaited_once()
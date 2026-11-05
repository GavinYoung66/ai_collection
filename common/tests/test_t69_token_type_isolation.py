"""T69 / 7.3：令牌类型隔离（refresh token 禁用为访问凭证）单测。

验收判据：
- refresh token 请求受保护接口语义（verify_token_with_reason）返回 401 链路的
  ``invalid token type``；verify_token 简版同步拒绝；
- access token 正常放行；
- 无 ``type`` 的历史 token 按 invalid 处理；
- 过期用例回归不变（exp 校验先行于 type 校验）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from common.config.settings import settings
from common.security.rbac import (
    create_refresh_token,
    create_token,
    verify_token,
    verify_token_with_reason,
)


class TestTokenTypeIsolation:
    def test_refresh_token_rejected_with_reason(self):
        """refresh token：verify_token_with_reason 返回 invalid token type。"""
        token = create_refresh_token("alice")
        payload, reason = verify_token_with_reason(token)
        assert payload is None
        assert reason == "invalid token type"
        assert verify_token(token) is None

    def test_access_token_accepted(self):
        """access token 正常放行（零回归）。"""
        token = create_token({"sub": "alice"})
        payload, reason = verify_token_with_reason(token)
        assert reason is None
        assert payload is not None and payload["sub"] == "alice"
        assert verify_token(token) is not None

    def test_legacy_token_without_type_rejected(self):
        """无 type 的历史 token：按 invalid 处理（不得作为访问凭证）。"""
        legacy = pyjwt.encode(
            {
                "sub": "old-user",
                "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        payload, reason = verify_token_with_reason(legacy)
        assert payload is None
        assert reason == "invalid token type"
        assert verify_token(legacy) is None

    def test_expired_access_token_still_reports_expiry_first(self):
        """过期 access token：exp 校验先行，reason 仍为 token expired（回归）。"""
        token = pyjwt.encode(
            {
                "sub": "alice",
                "type": "access",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
            },
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        payload, reason = verify_token_with_reason(token)
        assert payload is None
        assert reason == "token expired"
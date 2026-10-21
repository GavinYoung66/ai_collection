"""common/security/authn.py 单元测试：JWT + API Key 双方案。"""
from datetime import datetime, timedelta, timezone

from jose import jwt

from common.security.authn import AuthResult, authenticate, authenticate_api_key, authenticate_jwt
from common.security.rbac import create_token


class TestJWT:
    def test_valid_jwt_passes(self, jwt_secret):
        token = create_token({"sub": "user-123", "role": "agent"})
        result = authenticate_jwt(token)
        assert isinstance(result, AuthResult)
        assert result.ok is True
        assert result.user_id == "user-123"
        assert result.role == "agent"
        assert result.reason is None

    def test_valid_jwt_user_id_from_user_id_field(self, jwt_secret):
        # user_id 兼容 sub 或 user_id（与 qc auth.user_uuid 一致）
        token = create_token({"user_id": "uid-999", "role": "admin"})
        result = authenticate_jwt(token)
        assert result.ok is True
        assert result.user_id == "uid-999"

    def test_expired_jwt_rejected(self, jwt_secret):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        token = jwt.encode({"sub": "u1", "exp": past}, jwt_secret, algorithm="HS256")
        result = authenticate_jwt(token)
        assert result.ok is False
        assert result.reason == "token expired"

    def test_forged_signature_rejected(self, jwt_secret):
        token = jwt.encode({"sub": "u1"}, "wrong_secret", algorithm="HS256")
        result = authenticate_jwt(token)
        assert result.ok is False
        assert result.reason == "invalid signature"

    def test_issuer_mismatch_rejected(self, jwt_secret, issuer):
        token = create_token({"sub": "u1", "iss": "https://evil.com"})
        result = authenticate_jwt(token)
        assert result.ok is False
        assert result.reason == "issuer mismatch"

    def test_issuer_match_passes(self, jwt_secret, issuer):
        token = create_token({"sub": "u1", "iss": issuer})
        result = authenticate_jwt(token)
        assert result.ok is True

    def test_audience_mismatch_rejected(self, jwt_secret, audience):
        token = create_token({"sub": "u1", "aud": "other-aud"})
        result = authenticate_jwt(token)
        assert result.ok is False
        assert result.reason == "audience mismatch"

    def test_audience_match_passes_str(self, jwt_secret, audience):
        token = create_token({"sub": "u1", "aud": audience})
        result = authenticate_jwt(token)
        assert result.ok is True

    def test_audience_match_passes_list(self, jwt_secret, audience):
        token = create_token({"sub": "u1", "aud": [audience, "other"]})
        result = authenticate_jwt(token)
        assert result.ok is True

    def test_issuer_audience_empty_skip_check(self, jwt_secret):
        # JWT_ISSUER/JWT_AUDIENCE 为空时不校验，零回归
        token = create_token({"sub": "u1"})
        result = authenticate_jwt(token)
        assert result.ok is True

    def test_verify_token_accepts_aud_claim(self, jwt_secret):
        # P2-2 回归：token 携带 aud 时 verify_token 不得被 jose 内置校验误拒
        from common.security.rbac import verify_token

        token = create_token({"sub": "u1", "aud": "any-aud"})
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "u1"


class TestAPIKey:
    def test_valid_api_key_passes(self, jwt_secret, api_keys, api_key_role):
        result = authenticate_api_key("ak-abc123")
        assert result.ok is True
        assert result.role == api_key_role
        assert result.user_id == "api_key:ak-abc12"
        assert result.reason is None

    def test_valid_api_key_second_candidate(self, jwt_secret, api_keys, api_key_role):
        result = authenticate_api_key("ak-def456")
        assert result.ok is True
        assert result.user_id == "api_key:ak-def45"

    def test_wrong_api_key_rejected(self, jwt_secret, api_keys):
        result = authenticate_api_key("ak-not-exist")
        assert result.ok is False
        assert result.reason == "invalid api key"

    def test_empty_api_keys_config_rejected(self, jwt_secret, monkeypatch):
        from common.config.settings import settings
        monkeypatch.setattr(settings, "API_KEYS", "")
        result = authenticate_api_key("any-key")
        assert result.ok is False
        assert result.reason == "invalid api key"


class TestAuthenticateDispatch:
    def test_missing_credentials_no_header(self):
        result = authenticate("")
        assert result.ok is False
        assert result.reason == "missing credentials"

    def test_missing_credentials_no_bearer(self):
        result = authenticate("Basic abc")
        assert result.ok is False
        assert result.reason == "missing credentials"

    def test_missing_credentials_empty_token(self):
        result = authenticate("Bearer ")
        assert result.ok is False
        assert result.reason == "missing credentials"

    def test_jwt_dispatch_valid(self, jwt_secret):
        token = create_token({"sub": "u1", "role": "agent"})
        result = authenticate(f"Bearer {token}")
        assert result.ok is True
        assert result.role == "agent"

    def test_jwt_dispatch_forged_no_fallback_to_key(self, jwt_secret, api_keys):
        # 伪造 JWT 形态走 JWT 分支，失败不回退 API Key
        token = jwt.encode({"sub": "u1"}, "wrong_secret", algorithm="HS256")
        result = authenticate(f"Bearer {token}")
        assert result.ok is False
        assert result.reason == "invalid signature"

    def test_api_key_dispatch_via_bearer(self, jwt_secret, api_keys, api_key_role):
        # 非三段式 token → API Key 分支
        result = authenticate("Bearer ak-abc123")
        assert result.ok is True
        assert result.role == api_key_role

    def test_api_key_dispatch_wrong(self, jwt_secret, api_keys):
        result = authenticate("Bearer ak-wrong")
        assert result.ok is False
        assert result.reason == "invalid api key"


class TestNoSecondJWTDecode:
    """authn.py 必须复用 rbac 验签，不得自带 jwt.decode / import jose。"""

    def test_authn_source_has_no_jose_or_decode(self):
        import inspect
        import common.security.authn as authn_mod

        src = inspect.getsource(authn_mod)
        assert "import jose" not in src
        assert "from jose" not in src
        assert "jwt.decode" not in src

    def test_api_key_compare_no_early_return(self):
        """API Key 比对循环内禁止 return（命中即停会造成时序泄露）。"""
        import ast
        import inspect
        import common.security.authn as authn_mod

        tree = ast.parse(inspect.getsource(authn_mod.authenticate_api_key))
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                has_return = any(isinstance(n, ast.Return) for n in ast.walk(node))
                assert not has_return, "比对循环必须遍历全部候选，禁止命中即 return"

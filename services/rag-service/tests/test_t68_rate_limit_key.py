"""T68 / 7.2：限流身份改服务端可信来源 单测。

验收判据：
- 伪造 100 个不同 ``X-User-Id`` 的匿名请求按 IP 维度仍被限流（key 空间不膨胀）；
- 合法 JWT → ``uid:`` 命名空间；refresh/伪造 token → ``ip:`` 命名空间；
- ``uid:``/``ip:`` 两命名空间隔离，MUST NOT 混用。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from starlette.requests import Request

from app.services.rate_limit import _user_key
from common.security.rbac import create_refresh_token, create_token


def _request(headers: dict[str, str], client=("10.0.0.1", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


class TestRateLimitUserKey:
    def test_forged_x_user_id_falls_to_ip_namespace(self):
        """伪造 100 个不同 X-User-Id：全部落 ip: 命名空间（key 空间不膨胀）。"""
        keys: set[str] = set()
        for i in range(100):
            req = _request({"X-User-Id": f"forger-{i}"})
            keys.add(_user_key(req))
        assert keys == {"ip:10.0.0.1"}, "伪造头不得创建新限流 key"

    def test_valid_token_gets_uid_namespace(self):
        """合法 JWT：验签后 sub 进 uid: 命名空间（X-User-Id 不采信）。"""
        token = create_token({"sub": "alice", "role": "admin"})
        req = _request({"Authorization": f"Bearer {token}", "X-User-Id": "impersonated"})
        assert _user_key(req) == "uid:alice"

    def test_refresh_token_falls_to_ip_namespace(self):
        """refresh token（T69 拒绝为访问凭证）：落入 ip: 命名空间。"""
        token = create_refresh_token("alice")
        req = _request({"Authorization": f"Bearer {token}"})
        assert _user_key(req) == "ip:10.0.0.1"

    def test_namespaces_isolated(self):
        """uid:/ip: 两命名空间隔离断言。"""
        token = create_token({"sub": "bob"})
        assert _user_key(_request({"Authorization": f"Bearer {token}"})).startswith("uid:")
        assert _user_key(_request({})).startswith("ip:")

    def test_same_ip_aggregated_regardless_of_headers(self):
        """同一 IP 伪造不同头/不同 token：全部聚合到同一 ip: key。"""
        keys = {
            _user_key(_request({"X-User-Id": "a"})),
            _user_key(_request({"X-User-Id": "b", "Authorization": "Bearer bogus"})),
            _user_key(_request({})),
        }
        assert keys == {"ip:10.0.0.1"}
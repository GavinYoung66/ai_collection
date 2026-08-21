"""接口权限校验组件测试：无凭证 401+B0002 / 角色不足 403+B0003 / ADMIN/SUPERVISOR 放行。"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.services import require_role
from common.security.rbac import create_token
from common.trace.middleware import TraceMiddleware


@pytest.fixture
def client():
    """最小化测试 app：仅挂载权限校验依赖 + 401/403 handler + 追踪中间件。"""
    app = FastAPI()
    app.add_middleware(TraceMiddleware)
    require_role.register_auth_handlers(app)

    @app.get("/protected")
    async def protected(
        auth: None = Depends(require_role.require_roles("ADMIN", "SUPERVISOR")),
    ):
        return {"ok": True}

    @app.get("/public")
    async def public():
        return {"ok": True}

    return TestClient(app)


def _token(role: str) -> str:
    return create_token({"user_id": "u-test", "username": "tester", "role": role})


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestRequireRole:
    def test_no_credential_returns_401_b0002(self, client):
        """未携带 Authorization 头：401 + B0002。"""
        resp = client.get("/protected")
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "请先登录"
        assert body["data"] is None
        assert body["trace_id"]

    def test_invalid_token_returns_401_b0002(self, client):
        """无效 JWT：401 + B0002。"""
        resp = client.get("/protected", headers=_auth_header("invalid.token.here"))
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"

    def test_agent_role_returns_403_b0003(self, client):
        """凭证有效但角色为 agent：403 + B0003。"""
        resp = client.get("/protected", headers=_auth_header(_token("agent")))
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "B0003"
        assert body["message"] == "无操作权限"
        assert body["data"] is None
        assert body["trace_id"]

    def test_admin_allowed(self, client):
        """ADMIN（小写 admin）正常放行，角色比较大小写不敏感。"""
        resp = client.get("/protected", headers=_auth_header(_token("admin")))
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_supervisor_allowed(self, client):
        """SUPERVISOR（小写 supervisor）正常放行。"""
        resp = client.get("/protected", headers=_auth_header(_token("supervisor")))
        assert resp.status_code == 200

    def test_public_route_untouched(self, client):
        """未挂载权限校验的路由不受影响。"""
        assert client.get("/public").status_code == 200

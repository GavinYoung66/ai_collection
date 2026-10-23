"""deploy/auth/main.py 的 pytest 单测：login / me 两个端点。

契约唯一依据：SSOT `frontend/前端架构.md` §5.0 A.1（成功码 int `0`，
凭证错误 401 `B0002`「用户名或密码错误」、停用 403 `B0003`、token 无效 401 `B0002`「请先登录」）。

只测不改被测代码：用 `app.dependency_overrides[get_session]` 注入最小 fake 会话，
绝不连接真实 PostgreSQL；密码哈希复用 `main.pwd_context`（bcrypt cost=12），
与 `seed_users.py` 同一算法，禁止明文比对。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from common.config.settings import settings
from common.db.models.user import User
from common.security.rbac import create_token, verify_token_with_reason
from deploy.auth.main import app, get_session, pwd_context

_CORRECT_PASSWORD = "Admin@2026"
_PASSWORD_HASH = pwd_context.hash(_CORRECT_PASSWORD)


def _make_user(
    *,
    username: str = "admin",
    password: str = _CORRECT_PASSWORD,
    real_name: str = "系统管理员",
    role: str = "admin",
    status: str = "active",
    is_delete: str = "0",
) -> User:
    """构造内存 User（仅测试用，不落库）。

    显式赋值 id / is_delete：列默认值（default=）仅在 DB insert 期生效，
    User(...) 直接实例化时这些属性为 None，需手动补上才能被端点正确判断。
    """
    user = User(
        username=username,
        password_hash=_PASSWORD_HASH if password == _CORRECT_PASSWORD else pwd_context.hash(password),
        real_name=real_name,
        role=role,
        status=status,
    )
    user.id = uuid.uuid4()
    user.is_delete = is_delete
    return user


class _FakeResult:
    """模拟 execute(select(...)) 的返回对象，仅暴露 login 用到的 scalar_one_or_none()。"""

    def __init__(self, scalar):
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class FakeSession:
    """最小 fake AsyncSession：覆盖 login(execute/scalar_one_or_none/commit) 与 me(get)。"""

    def __init__(self, login_user=None, me_users=None):
        self.login_user = login_user
        self.me_users = me_users or {}
        self.committed = False

    async def execute(self, _statement):
        return _FakeResult(self.login_user)

    async def commit(self):
        self.committed = True

    async def get(self, _entity, pk):
        return self.me_users.get(str(pk))


class _DB:
    """承载每个测试要注入的 fake 会话；测试先赋值 .session，再发请求。"""

    def __init__(self):
        self.session = None


@pytest.fixture()
def fake_db():
    """安装 get_session 覆盖，完成后清理，避免串扰其它测试。"""
    holder = _DB()
    app.dependency_overrides[get_session] = lambda: holder.session
    yield holder
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


class TestLogin:
    """POST /api/v1/auth/login 的 4 个判据用例（tasks.md 1.5）。"""

    def _post(self, client, username, password):
        return client.post("/api/v1/auth/login", json={"username": username, "password": password})

    def test_success(self, client, fake_db):
        user = _make_user(username="admin", role="admin")
        fake_db.session = FakeSession(login_user=user)

        resp = self._post(client, "admin", _CORRECT_PASSWORD)

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert isinstance(body["code"], int)  # 成功码为 int 0，非字符串

        data = body["data"]
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == settings.JWT_EXPIRE_HOURS * 3600

        # access_token 可被 verify_token_with_reason 成功解析，且载荷正确
        payload, reason = verify_token_with_reason(data["access_token"])
        assert reason is None
        assert payload is not None
        assert payload["sub"] == str(user.id)
        assert payload["role"] == "admin"

        assert data["user"] == {
            "user_id": str(user.id),
            "username": "admin",
            "real_name": "系统管理员",
            "role": "admin",
            "status": "active",
        }

    def test_wrong_password(self, client, fake_db):
        fake_db.session = FakeSession(login_user=_make_user(username="admin"))

        resp = self._post(client, "admin", "wrong-password")

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "用户名或密码错误"
        assert body["data"] is None

    def test_nonexistent_user_same_message(self, client, fake_db):
        # 防枚举：用户不存在与密码错误返回完全一致的 401 信封
        fake_db.session = FakeSession(login_user=None)

        resp = self._post(client, "nonexistent", "whatever")

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "用户名或密码错误"
        assert body["data"] is None

    @pytest.mark.parametrize("status", ["disabled", "locked"])
    def test_disabled_or_locked(self, client, fake_db, status):
        fake_db.session = FakeSession(login_user=_make_user(status=status))

        resp = self._post(client, "admin", _CORRECT_PASSWORD)

        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "B0003"
        assert body["message"] == "账号已停用或锁定"
        assert body["data"] is None

    def test_missing_fields_422(self, client, fake_db):
        # spec「空用户名或空密码参数校验」：422 + B0001，且不触发 DB 查询
        fake_db.session = FakeSession(login_user=None)

        resp = self._post(client, "", "")

        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "B0001"
        assert body["message"] == "参数校验失败"


class TestMe:
    """GET /api/v1/auth/me 的判据用例（tasks.md 1.5 + spec 补充）。"""

    def _get(self, client, token):
        return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    def test_valid_token_returns_db_role(self, client, fake_db):
        # 库内当前 role=agent，token 内旧值 admin：必须返回库值，防「权限回收但 token 未过期」
        user = _make_user(username="admin", role="agent")
        fake_db.session = FakeSession(me_users={str(user.id): user})
        token = create_token({"sub": str(user.id), "role": "admin"})

        resp = self._get(client, token)

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        # me 的 data 为单个 user 对象（SSOT A.1），字段与 login 一致
        assert body["data"] == {
            "user_id": str(user.id),
            "username": "admin",
            "real_name": "系统管理员",
            "role": "agent",  # 库内当前值，非 token 旧值 admin
            "status": "active",
        }

    def test_expired_token_401(self, client, fake_db):
        expired = jwt.encode(
            {"sub": str(uuid.uuid4()), "role": "admin", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            settings.JWT_SECRET,
            algorithm=settings.JWT_ALGORITHM,
        )
        fake_db.session = FakeSession(me_users={})

        resp = self._get(client, expired)

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "请先登录"
        assert "reason" not in body  # 内部 reason 不向客户端暴露
        assert body["data"] is None

    def test_forged_signature_401(self, client, fake_db):
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "role": "admin"},
            "wrong-secret",
            algorithm=settings.JWT_ALGORITHM,
        )
        fake_db.session = FakeSession(me_users={})

        resp = self._get(client, forged)

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "请先登录"
        assert "reason" not in body

    @pytest.mark.parametrize("kind", ["missing", "deleted"])
    def test_user_missing_or_deleted_401(self, client, fake_db, kind):
        # 用户不存在 / is_delete 与 token 无效同口径 401，不区分、不泄露
        if kind == "missing":
            token = create_token({"sub": str(uuid.uuid4()), "role": "admin"})
            fake_db.session = FakeSession(me_users={})
        else:
            user = _make_user(is_delete="1")
            token = create_token({"sub": str(user.id), "role": "admin"})
            fake_db.session = FakeSession(me_users={str(user.id): user})

        resp = self._get(client, token)

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "请先登录"
        assert body["data"] is None

    def test_no_token_401(self, client, fake_db):
        fake_db.session = FakeSession(me_users={})

        resp = client.get("/api/v1/auth/me")

        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "B0002"
        assert body["message"] == "请先登录"

    def test_disabled_user_403(self, client, fake_db):
        user = _make_user(status="disabled")
        token = create_token({"sub": str(user.id), "role": "admin"})
        fake_db.session = FakeSession(me_users={str(user.id): user})

        resp = self._get(client, token)

        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "B0003"
        assert body["message"] == "账号已停用或锁定"
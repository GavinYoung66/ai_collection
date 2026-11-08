"""T115：pg_dsn 用户/密码 URL 编码（spec runtime-correctness：DSN 构造单源收敛）。

密码含 `@ : / # ?` 等保留字符时未编码拼接会被 SQLAlchemy URL 解析错位
（启动失败或连错 host）；三个 dsn property 统一 quote(safe="") 包裹。
另覆盖：空格密码（quote_plus 的 `+` 语义错位）与 Alembic ConfigParser `%` 插值。
"""
from __future__ import annotations

from alembic.config import Config
from sqlalchemy.engine import make_url

import pytest

from common.config.settings import settings

_RESERVED_PASSWORD = "p@ss:wo/rd#1?2"
_RESERVED_USER = "user@name"
_SPACE_PASSWORD = "p w@ss/1"


class TestT115PgDsnQuote:
    def setup_method(self):
        self._patched = []

    def teardown_method(self):
        pass

    def _patch(self, monkeypatch):
        monkeypatch.setattr(settings, "PG_USER", _RESERVED_USER)
        monkeypatch.setattr(settings, "PG_PASSWORD", _RESERVED_PASSWORD)
        monkeypatch.setattr(settings, "PG_HOST", "db.internal")
        monkeypatch.setattr(settings, "PG_PORT", 5432)
        monkeypatch.setattr(settings, "PG_DB", "app")

    def test_pg_dsn_password_encoded_host_not_misaligned(self, monkeypatch):
        """spec 场景：密码含保留字符时连接串正确转义并连接到预期主机。"""
        self._patch(monkeypatch)
        url = make_url(settings.pg_dsn)
        assert url.username == _RESERVED_USER
        assert url.password == _RESERVED_PASSWORD
        assert url.host == "db.internal"
        assert url.port == 5432
        assert url.database == "app"

    def test_pg_dsn_sync_password_encoded(self, monkeypatch):
        self._patch(monkeypatch)
        url = make_url(settings.pg_dsn_sync)
        assert url.password == _RESERVED_PASSWORD
        assert url.host == "db.internal"

    def test_pg_dsn_psycopg3_password_encoded(self, monkeypatch):
        self._patch(monkeypatch)
        # 裸 DSN（无 +driver）：libpq 语义，同样经 make_url 验证转义
        url = make_url(settings.pg_dsn_psycopg3)
        assert url.password == _RESERVED_PASSWORD
        assert url.host == "db.internal"
        assert not settings.pg_dsn_psycopg3.startswith("postgresql+")

    def test_plain_password_roundtrip_unchanged(self, monkeypatch):
        """普通密码（无保留字符）：编码前后语义等价（零回归）。"""
        monkeypatch.setattr(settings, "PG_USER", "app")
        monkeypatch.setattr(settings, "PG_PASSWORD", "plain-pass-123")
        monkeypatch.setattr(settings, "PG_HOST", "db.internal")
        monkeypatch.setattr(settings, "PG_PORT", 5432)
        monkeypatch.setattr(settings, "PG_DB", "app")
        url = make_url(settings.pg_dsn)
        assert url.username == "app"
        assert url.password == "plain-pass-123"
        assert url.host == "db.internal"

    def test_space_password_not_encoded_as_plus(self, monkeypatch):
        """空格密码：必须编码为 %20 而非 `+`，否则密码语义错位。"""
        self._patch(monkeypatch)
        monkeypatch.setattr(settings, "PG_PASSWORD", _SPACE_PASSWORD)
        dsn = settings.pg_dsn
        password_part = dsn.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        assert "%20" in password_part and "+" not in password_part
        assert make_url(dsn).password == _SPACE_PASSWORD

    def test_alembic_configparser_percent_roundtrip(self, monkeypatch):
        """env.py 的 `%` → `%%` 转义经 ConfigParser 还原后 DSN 语义不变。"""
        self._patch(monkeypatch)
        monkeypatch.setattr(settings, "PG_PASSWORD", _SPACE_PASSWORD)  # 含 %20
        cfg = Config()
        cfg.set_main_option("sqlalchemy.url", settings.pg_dsn.replace("%", "%%"))
        url = make_url(cfg.get_main_option("sqlalchemy.url"))
        assert url.password == _SPACE_PASSWORD
        assert url.host == "db.internal"

    def test_alembic_configparser_unescaped_percent_raises(self, monkeypatch):
        """反证：未转义的 `%20` 会被 ConfigParser 当成插值语法（ValueError）。"""
        self._patch(monkeypatch)
        monkeypatch.setattr(settings, "PG_PASSWORD", _SPACE_PASSWORD)
        cfg = Config()
        with pytest.raises(ValueError):
            cfg.set_main_option("sqlalchemy.url", settings.pg_dsn)
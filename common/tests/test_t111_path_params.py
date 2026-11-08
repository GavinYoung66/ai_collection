"""T111：URL path 参数白名单单源（spec runtime-correctness：单源收敛完整性）。

时间解析、URL path 参数白名单与 DSN 构造 MUST 复用单一事实源，MUST NOT 在
调用点保留等价本地实现。qc→dp（T79）与 dp→qc/complaint（T111）共用
``common.security.path_params``。
"""
from __future__ import annotations

import pytest

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.security.path_params import (
    PATH_PARAM_RE,
    ensure_safe_path_param,
    is_safe_path_param,
)


class TestT111PathParams:
    def test_valid_ulid_and_alnum(self):
        """合法 ULID / 字母数字 / *-_ 形态放行（spec 场景：合法 ULID 正常）。"""
        assert is_safe_path_param("01HZZZZZZZZZZZZZZZZZZZZZZZ")
        assert is_safe_path_param("abc-DEF-123")
        assert is_safe_path_param("a*b")
        assert is_safe_path_param("sess_fixture_001")

    def test_invalid_traversal_and_semantics_chars(self):
        """路径穿越与改写请求语义的字符一律拒绝（spec 场景）。"""
        assert not is_safe_path_param("x/../y")
        assert not is_safe_path_param("x.y")
        assert not is_safe_path_param("..")
        assert not is_safe_path_param("x?y")
        assert not is_safe_path_param("x#y")
        assert not is_safe_path_param("")
        assert not is_safe_path_param(None)
        assert not is_safe_path_param("a" * 65)  # 超长（VARCHAR(64) 对齐）

    def test_ensure_raises_a2002_on_illegal(self):
        with pytest.raises(BizException) as ei:
            ensure_safe_path_param("x/../y", name="session_id")
        assert ei.value.code == ErrorCode.A2002

    def test_ensure_returns_value_unchanged(self):
        assert ensure_safe_path_param("abc-123", name="complaint_id") == "abc-123"

    def test_regex_shape_anchored(self):
        """白名单正则口径锚定：^[A-Za-z0-9*_-]{1,64}$（T79/T111 共用）。"""
        assert PATH_PARAM_RE.pattern == r"^[A-Za-z0-9*_-]{1,64}$"
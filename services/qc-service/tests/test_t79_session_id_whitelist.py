"""T79 / 7.5：qc-service 拼 URL path 参数白名单校验 单测。

验收判据：``"x/../y"`` 被拒绝（BizException）；合法 ULID 正常拼 URL；
超长/空串同样拒绝。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import pytest

from app.integrations.dp_client import DpClient
from common.errors.exceptions import BizException


class TestSessionIdWhitelist:
    def test_path_traversal_rejected(self):
        """``x/../y`` 被拒绝（BizException，A2002）。"""
        client = DpClient(base_url="http://dp:8000")
        with pytest.raises(BizException):
            client.qc_source_url("x/../y")

    def test_valid_ulid_builds_url(self):
        """合法 ULID：正常拼 URL。"""
        client = DpClient(base_url="http://dp:8000")
        url = client.qc_source_url("01J9Z8Q4GK5VQ2M7S3XWY0P1AB")
        assert "01J9Z8Q4GK5VQ2M7S3XWY0P1AB" in url
        assert url.startswith("http://dp:8000")

    def test_overlong_rejected(self):
        """65 字符（超 64 上限）：拒绝。"""
        client = DpClient(base_url="http://dp:8000")
        with pytest.raises(BizException):
            client.qc_source_url("a" * 65)

    def test_empty_rejected(self):
        """空串：拒绝。"""
        client = DpClient(base_url="http://dp:8000")
        with pytest.raises(BizException):
            client.qc_source_url("")

    def test_star_allowed_by_whitelist(self):
        """``*`` 在白名单字符集内（允许通配形态）。"""
        client = DpClient(base_url="http://dp:8000")
        url = client.qc_source_url("2026*-batch")
        assert "2026*-batch" in url
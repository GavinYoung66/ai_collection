"""A/B 流量管理单元测试（公共 prompt.ab）。"""
from __future__ import annotations

from common.prompt import ab


def setup_function():
    """每个测试前清空 A/B 配置。"""
    ab._ab_config.clear()


def test_ab_distribution_ratio():
    """A/B 比例在合理区间内（±10%）。"""
    ab.configure_ab("complaint_verdict", {"1.1.0": 90, "1.2.0": 10})
    counts = {"1.1.0": 0, "1.2.0": 0}
    n = 10000
    for i in range(n):
        v = ab.select_version("complaint_verdict", f"session_{i}")
        counts[v] += 1
    ratio_11 = counts["1.1.0"] / n
    ratio_12 = counts["1.2.0"] / n
    assert 0.80 <= ratio_11 <= 1.00, f"1.1.0 ratio {ratio_11} out of range"
    assert 0.00 <= ratio_12 <= 0.20, f"1.2.0 ratio {ratio_12} out of range"


def test_same_session_stable():
    """同一会话多次请求命中同一版本。"""
    ab.configure_ab("complaint_verdict", {"1.1.0": 50, "1.2.0": 50})
    v1 = ab.select_version("complaint_verdict", "stable_session_123")
    for _ in range(100):
        assert ab.select_version("complaint_verdict", "stable_session_123") == v1


def test_rollback_immediate():
    """rollback 后全部返回指定版本。"""
    ab.configure_ab("complaint_verdict", {"1.1.0": 90, "1.2.0": 10})
    ab.rollback("complaint_verdict", "1.0.0")
    for i in range(1000):
        assert ab.select_version("complaint_verdict", f"s{i}") == "1.0.0"


def test_configure_invalid_sum():
    """百分比总和非 100 抛 ValueError。"""
    import pytest

    with pytest.raises(ValueError):
        ab.configure_ab("x", {"1.0.0": 80})


def test_no_config_raises():
    """未配置 A/B 时 select_version 抛 KeyError。"""
    import pytest

    with pytest.raises(KeyError):
        ab.select_version("unconfigured_pid", "s1")

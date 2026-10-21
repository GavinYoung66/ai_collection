"""3.8 回归：qc-service /metrics 统一指标（svc 标签精确匹配，防手工验证漂移）。"""
from common.tests._metrics_regression import assert_unified_metrics_endpoint


def test_metrics_endpoint_unified_http_metrics():
    assert_unified_metrics_endpoint("qc-service")

"""8 服务 /metrics 统一指标回归的共享断言（openspec tasks.md 3.8 唯一事实源）。

各服务 tests/ 下的 test_metrics_<svc>.py 仅一行调用本断言；
断言经 prometheus_client.parser 结构化解析，与 exposition 的标签书写顺序无关。
注意：parser 对 counter 的 family 名剥离 _total 后缀（样本名保留全名），
故按样本名 http_requests_total 匹配；/metrics 自身请求的计数在响应体生成之后
才累加，需先打一发 /health 预热产生样本。
"""
from __future__ import annotations

from prometheus_client.parser import text_string_to_metric_families


def assert_unified_metrics_endpoint(expected_svc: str) -> None:
    """对当前服务 app 断言 GET /metrics：200 + 原生 exposition + 统一指标 + svc 标签精确匹配。"""
    # 延迟导入：调用方（对应服务 tests）已把服务根装入 sys.path
    from fastapi.testclient import TestClient

    from app.main import app  # type: ignore[import-untyped]  # 动态服务包，无 stubs/py.typed

    client = TestClient(app)
    # 预热：/metrics 响应体生成时本请求尚未计数，需一次先前请求产生样本
    assert client.get("/health").status_code == 200

    resp = client.get("/metrics")
    assert resp.status_code == 200
    # 原生 exposition（CONTENT_TYPE_LATEST），SHALL NOT 是 JSON 统一信封
    assert resp.headers["content-type"].startswith("text/plain"), (
        f"/metrics Content-Type 异常：{resp.headers['content-type']!r}"
    )

    svc_labels = {
        sample.labels.get("svc")
        for family in text_string_to_metric_families(resp.text)
        for sample in family.samples
        if sample.name == "http_requests_total"
    }
    assert svc_labels, "body 缺少统一指标 http_requests_total 样本"
    assert svc_labels == {expected_svc}, (
        f"http_requests_total 的 svc 标签期望仅 {expected_svc!r}，实际 {sorted(map(str, svc_labels))!r}"
        "（检查 mount_metrics 的 service_name 是否被改坏或双重注册退化）"
    )

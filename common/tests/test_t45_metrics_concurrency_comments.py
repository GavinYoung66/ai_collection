"""T45：metrics.py 惰性模板缓存与模块级默认实例的并发假设注释守护。

验收判据：缓存与全局实例处有并发假设注释（评审结论「可接受，加注释说明即可」）。
行为 MUST NOT 变化：本测试仅钉注释存在与既有惰性缓存语义。
"""
from __future__ import annotations

import ast
import inspect

from common.observability.metrics import (
    DEFAULT_HTTP_METRICS,
    HTTPMetrics,
    MetricsMiddleware,
    REGISTRY,
)

_metrics_module = inspect.getmodule(HTTPMetrics)
assert _metrics_module is not None  # 测试自身依赖模块可定位源码
MODULE_SRC = inspect.getsource(_metrics_module)


class TestConcurrencyAssumptionComments:
    """验证判据：两处并发假设注释存在。"""

    def test_default_instance_has_concurrency_comment(self):
        """全局实例处：DEFAULT_HTTP_METRICS 上方注释块含「并发假设」及进程/导入锁语义。"""
        lines = MODULE_SRC.splitlines()
        idx = next(i for i, ln in enumerate(lines) if ln.startswith("DEFAULT_HTTP_METRICS ="))
        block = "\n".join(lines[max(0, idx - 12):idx])
        assert "并发假设" in block, "DEFAULT_HTTP_METRICS 定义处缺少并发假设注释"
        assert "导入锁" in block and "多进程" in block, "全局实例注释须覆盖导入锁与多进程假设"

    def test_lazy_cache_docstring_has_concurrency_assumption(self):
        """缓存处：_resolve_templates 文档含「并发假设」且说明无 await 挂起点。"""
        doc = inspect.getdoc(MetricsMiddleware._resolve_templates) or ""
        assert "并发假设" in doc, "_resolve_templates 缺少并发假设说明"
        assert "await" in doc and "原子" in doc, "缓存注释须解释 check-then-act 无挂起点"


async def _noop_asgi_app(scope, receive, send) -> None:  # 满足 ASGIApp 签名
    return None


class TestBehaviorUnchanged:
    """回归：加注释不改变既有惰性缓存语义。"""

    def _middleware(self, **kw) -> MetricsMiddleware:
        return MetricsMiddleware(app=_noop_asgi_app, service_name="svc", **kw)

    def test_cache_resolved_once_and_idempotent(self):
        """正常流程：首次调用派生并缓存，后续调用返回同一 tuple 对象。"""
        mw = self._middleware(path_templates=["/a", "/b/{id}"])
        first = mw._resolve_templates()
        second = mw._resolve_templates()
        assert first == second
        assert first is second  # 命中缓存，未重复派生

    def test_no_source_falls_back_to_base_templates(self):
        """边界场景：无 override 无 fastapi_app → 仅基础模板 + extra，仍缓存。"""
        mw = self._middleware(extra_path_templates=["/extra"])
        t1 = mw._resolve_templates()
        assert "/extra" in t1 and t1 is mw._resolve_templates()

    def test_concurrent_first_requests_share_cache(self):
        """并发场景：协程并发首请求下缓存结果一致（无 await 挂起点 → 无交错）。"""
        import asyncio

        mw = self._middleware(path_templates=["/x"])

        async def gather_results():
            return await asyncio.gather(*(asyncio.to_thread(mw._resolve_templates) for _ in range(8)))

        results = asyncio.run(gather_results())
        assert all(r == results[0] for r in results)

    def test_default_instance_registered_on_default_registry(self):
        """正常流程：模块级默认实例仍注册在默认 REGISTRY（注释改动不影响注册）。"""
        assert DEFAULT_HTTP_METRICS is not None
        names = {m.name for m in REGISTRY.collect()}
        # Counter 采集名为基名（_total 后缀由 prometheus_client 在文本层追加）
        assert "http_requests" in names


def test_module_importable_via_ast():
    """守护：模块可被 AST 解析（注释未破坏语法结构）。"""
    ast.parse(MODULE_SRC)

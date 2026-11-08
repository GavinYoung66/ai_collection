"""鉴权矩阵守护测试（4.1）：data-platform 全路由扫描。

- 收口路由（T91）MUST 挂 require_service_credential 依赖
- 豁免白名单显式枚举（path, method），禁止通配：仅 health/metrics
"""
from fastapi.routing import APIRoute

from app.main import app

# 收口路由（本批 9 组中 dp 部分）：MUST 有鉴权依赖
_REQUIRED_AUTH_ROUTES = {
    ("GET", "/api/v1/data/sessions/{session_id}/qc-source"),
    ("GET", "/api/v1/data/sessions:pending-qc"),
    ("GET", "/api/v1/data/snapshot/{case_id}"),
    ("PUT", "/api/v1/data/snapshot/{case_id}"),
}

# 豁免白名单：显式枚举，附理由（T91：豁免仅 health）
_EXEMPT_ROUTES = {
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    ("GET", "/health"),  # 探活（T91 豁免仅 health）
    ("GET", "/api/v1/health"),  # 带前缀探活
    ("GET", "/metrics"),  # Prometheus 拉取端点
}

# 鉴权依赖识别标记（匹配 dependant 树中 call 的 __qualname__/__name__）
_AUTH_MARKERS = ("require_service_credential",)


def _routes() -> dict[tuple[str, str], APIRoute]:
    result: dict[tuple[str, str], APIRoute] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or set():
                result.setdefault((method, route.path), route)
    return result


def _has_auth_dep(route: APIRoute) -> bool:
    """递归检查 dependant 树（含参数级 Depends）是否存在鉴权依赖。"""
    seen: set[int] = set()

    def walk(dep) -> bool:  # noqa: ANN001
        if id(dep) in seen:
            return False
        seen.add(id(dep))
        call = dep.call
        name = getattr(call, "__qualname__", "") or getattr(call, "__name__", "")
        if any(marker in name for marker in _AUTH_MARKERS):
            return True
        return any(walk(sub) for sub in dep.dependencies)

    return any(walk(d) for d in route.dependant.dependencies)


class TestAuthMatrix:
    def test_batch123_hardened_routes_have_auth(self):
        routes = _routes()
        missing = []
        for key in sorted(_REQUIRED_AUTH_ROUTES):
            route = routes.get(key)
            if route is None:
                missing.append(f"{key}: route missing")
            elif not _has_auth_dep(route):
                missing.append(f"{key}: no auth dependency")
        assert missing == []

    def test_all_routes_covered_by_auth_or_whitelist(self):
        routes = _routes()
        offenders = []
        for key in sorted(routes):
            if key in _REQUIRED_AUTH_ROUTES or key in _EXEMPT_ROUTES:
                continue
            if not _has_auth_dep(routes[key]):
                offenders.append(f"{key[0]} {key[1]}")
        assert offenders == [], f"routes without auth and not whitelisted: {offenders}"
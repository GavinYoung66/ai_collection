"""鉴权矩阵守护测试（4.1）：qc-service 全路由扫描。

- 收口路由（T87/T88）与既有鉴权路由 MUST 挂鉴权依赖（含参数级 Depends，
  经 dependant 树递归识别）
- 豁免白名单显式枚举（path, method），禁止通配
- 新增收口路由必须同步进矩阵覆盖
"""
from fastapi.routing import APIRoute

from app.main import app

# 收口路由（本批 9 组中 qc 部分）：MUST 有鉴权依赖
_REQUIRED_AUTH_ROUTES = {
    ("POST", "/api/v1/qc/demo/seed"),
    ("GET", "/api/v1/qc/demo/pending"),
    ("GET", "/api/v1/qc/demo/{session_id}/trace"),
    ("POST", "/api/v1/qc/replay/raw"),
    ("POST", "/api/v1/qc/replay/{session_id}"),
    ("POST", "/api/v1/qc/check/batch"),
}

# 豁免白名单：显式枚举，附理由
_EXEMPT_ROUTES = {
    ("GET", "/openapi.json"),  # FastAPI schema
    ("GET", "/docs"),  # API 文档
    ("GET", "/docs/oauth2-redirect"),  # OAuth2 重定向
    ("GET", "/redoc"),  # API 文档
    ("GET", "/health"),  # 探活（服务发现）
    ("GET", "/metrics"),  # Prometheus 拉取端点（spec platform/observability）
    ("GET", "/mcp"),  # qc 内部 MCP server 端点（供 mcp-gateway 内网调用，网络隔离防护；鉴权属后续批次范围）
    ("POST", "/mcp"),  # 同上
}

# 鉴权依赖识别标记（匹配 dependant 树中 call 的 __qualname__/__name__）
_AUTH_MARKERS = ("require_roles", "demo_gate")


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
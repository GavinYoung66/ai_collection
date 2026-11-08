"""T142：complaint 同步探活处置（保留 + 注释固化 + AST 守护兜底）。

- 现场 grep 复核：`health_check()` 生产零调用点（死代码定性成立），保留形态处置
- `MCPGateway` 类经 `app/api/deps.py:26` 被生产引用——类可导入、`get_shared_client` 正常
- AST 断言：gateway.py 的 `async def` 函数体内无同步 `httpx.get`（4.2 规则 2 同型）
- `USE_MOCK_MCP=true` 固定 healthy 不发网络请求语义保持
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.mcp.gateway import MCPGateway

_GATEWAY_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "mcp" / "gateway.py"
)


class TestDeadCodeDisposition:
    def test_health_check_method_preserved_with_verdict_docstring(self):
        """保留形态：方法存在且 docstring 固化 T142 处置裁决。"""
        doc = (MCPGateway.health_check).__doc__ or ""
        assert "T142" in doc
        assert "asyncio.to_thread" in doc
        assert "ahealth_check" in doc

    def test_gateway_class_importable_and_shared_client_alive(self):
        """类可导入 + get_shared_client 引用回归（deps.py 经实例方法调用，留痕于基线状态行）。"""
        assert MCPGateway is not None
        assert hasattr(MCPGateway, "get_shared_client")


class TestASTGuards:
    def test_no_sync_httpx_get_in_async_functions(self):
        """AST：gateway.py 的 async def 函数体内不得出现同步 httpx.get 探活。"""
        tree = ast.parse(_GATEWAY_PATH.read_text(encoding="utf-8"))

        offenders: list[str] = []

        def walk(node, in_async: bool) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)):
                    walk(child, in_async or isinstance(child, ast.AsyncFunctionDef))
                    continue
                if (
                    in_async
                    and isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "get"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "httpx"
                ):
                    offenders.append(f"line {child.lineno}")
                walk(child, in_async)

        walk(tree, False)
        assert offenders == [], f"sync httpx.get inside async def: {offenders}"

    def test_mock_branch_sends_no_network_request(self, monkeypatch):
        """USE_MOCK_MCP=true 固定 healthy 分支不发网络请求（既有语义保持）。"""
        from app.config import settings

        monkeypatch.setattr(settings, "USE_MOCK_MCP", True, raising=False)

        def _forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("mock branch must not hit network")

        monkeypatch.setattr("app.mcp.gateway.httpx.get", _forbidden)
        monkeypatch.setattr("app.mcp.gateway.httpx.HTTPError", Exception)

        gw = MCPGateway.__new__(MCPGateway)
        gw._gateway_url = "http://mcp-gateway:9000"
        gw._health = {}
        result = gw.health_check()
        assert set(result.values()) == {"healthy"}
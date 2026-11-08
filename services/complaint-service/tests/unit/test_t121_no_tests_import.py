"""T121：complaint 生产代码去 tests.* 依赖（audit-findings-remediation-r3 8.2）。

- AST 扫描 app/ 下所有 .py 无 ``from tests`` / ``import tests`` 导入
- USE_MOCK_MCP=true 下 MockMCPClient 可从 app.mock 导入、实例化并加载 fixture
- USE_MOCK_LLM 下 MockLLMProvider 可从 app.mock 导入并实例化
- seed_complaints_v2 从 app.mock 导入可用（数据结构完整）
- tests/mock 薄转发层回归：旧导入路径零改动可用且指向同一对象
- Dockerfile 形态断言：不再 COPY tests/ 目录，app/（含 app/mock/）整体进镜像
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _SERVICE_ROOT / "app"
_DOCKERFILE = _SERVICE_ROOT / "Dockerfile"


def _iter_app_py_files() -> list[Path]:
    return sorted(p for p in _APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


class TestNoTestsImportInApp:
    def test_ast_scan_app_no_tests_import(self):
        """app/ 全量 AST 扫描：无 from tests / import tests 导入节点。"""
        offenders: list[str] = []
        for path in _iter_app_py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    if any(alias.name.split(".")[0] == "tests" for alias in node.names):
                        offenders.append(f"{path.name}:{node.lineno} import {node.names[0].name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] == "tests":
                        offenders.append(f"{path.name}:{node.lineno} from {node.module}")
        assert offenders == [], f"app/ 仍存在 tests.* 导入: {offenders}"

    def test_app_mock_package_structure(self):
        """迁移闭包完整：app/mock 各模块与 fixtures 目录就位。"""
        for rel in (
            "app/mock/__init__.py",
            "app/mock/mcp/mock_client.py",
            "app/mock/mcp/errors.py",
            "app/mock/llm/mock_provider.py",
            "app/mock/data/seed_complaints_v2.py",
        ):
            assert (_SERVICE_ROOT / rel).is_file(), rel
        assert (_APP_DIR / "mock" / "mcp" / "fixtures").is_dir()
        assert (_APP_DIR / "mock" / "llm" / "fixtures").is_dir()
        assert len(list((_APP_DIR / "mock" / "mcp" / "fixtures").glob("*.json"))) > 0
        assert len(list((_APP_DIR / "mock" / "llm" / "fixtures").glob("*.json"))) > 0


class TestMockImportsFromAppMock:
    def test_mock_mcp_client_importable_and_instantiable(self):
        """USE_MOCK_MCP=true：MockMCPClient 从 app.mock 导入并可实例化。"""
        from app.config import settings
        from app.mcp.gateway import MCPGateway
        from app.mock.mcp.mock_client import MockMCPClient

        assert getattr(settings, "USE_MOCK_MCP", True) is True
        client = MockMCPClient()
        assert client is not None

        gateway = MCPGateway()
        transport = gateway._build_transport("business_ticket")
        assert isinstance(transport, MockMCPClient)

    def test_mock_mcp_client_loads_fixture_from_app_mock(self):
        """默认启动路径可用：迁移后 fixture 从 app/mock/mcp/fixtures 加载。"""
        from app.mock.mcp.mock_client import MockMCPClient

        client = MockMCPClient()
        result = asyncio.run(
            client.call("business_ticket", "query_case_info", {"case_id": "case-001"})
        )
        assert isinstance(result, dict) and result

    def test_mock_llm_provider_importable_and_instantiable(self):
        """USE_MOCK_LLM=true：MockLLMProvider 从 app.mock 导入并实例化。"""
        from app.config import settings
        from app.llm.provider import ComplaintLLMProvider
        from app.mock.llm.mock_provider import MockLLMProvider

        assert getattr(settings, "USE_MOCK_LLM", True) is True
        provider = MockLLMProvider()
        assert provider is not None
        assert isinstance(ComplaintLLMProvider()._get_transport(), MockLLMProvider)

    def test_seed_complaints_v2_importable_from_app_mock(self):
        """seed_complaints_v2 从 app.mock 导入：数据结构完整。"""
        from app.mock.data.seed_complaints_v2 import (
            SCENARIO_PENDING,
            V2_SEED_COMPLAINTS,
            iter_demo_seed_creates,
        )

        assert len(V2_SEED_COMPLAINTS) >= 4
        seeds = iter_demo_seed_creates()
        assert len(seeds) == 3
        required_keys = {"complaint_text", "case_id", "channel", "scenario"}
        for seed in seeds:
            assert required_keys <= set(seed), seed
        assert any(s["scenario"] == SCENARIO_PENDING for s in seeds)


class TestForwardingLayerRegression:
    def test_legacy_tests_mock_import_paths_still_work(self):
        """旧导入路径（tests.mock.*）零改动可用，且与 app.mock 同一对象。"""
        from app.mock.llm.mock_provider import MockLLMProvider as AppMockLLM
        from app.mock.mcp.mock_client import MockMCPClient as AppMockMCP
        from tests.mock.data.seed_complaints_v2 import (  # noqa: F401
            SCENARIO_PENDING,
            iter_demo_seed_creates,
        )
        from tests.mock.llm.mock_provider import MockLLMProvider as TestsMockLLM
        from tests.mock.mcp.mock_client import (  # noqa: F401
            ALL_TOOLS,
            SERVERS,
            MockMCPClient as TestsMockMCP,
        )
        from tests.mock.mcp.errors import MCPError, MCPTimeoutError

        assert TestsMockMCP is AppMockMCP
        assert TestsMockLLM is AppMockLLM
        assert SERVERS and ALL_TOOLS
        assert MCPError and MCPTimeoutError
        assert SCENARIO_PENDING and callable(iter_demo_seed_creates)


class TestDockerfileShape:
    def test_dockerfile_no_tests_copy(self):
        """镜像不再 COPY tests/ 目录（含 tests/mock 兜底）。"""
        content = _DOCKERFILE.read_text(encoding="utf-8")
        assert "tests/__init__.py" not in content
        assert "tests/mock/" not in content
        assert "COPY services/complaint-service/tests" not in content

    def test_dockerfile_copies_app_whole(self):
        """app/ 整体 COPY（app/mock/ 随之进镜像，默认启动不 ImportError）。"""
        content = _DOCKERFILE.read_text(encoding="utf-8")
        assert "COPY services/complaint-service/app/ ./app/" in content
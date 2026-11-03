"""T38 门禁：契约断言保留 + 真实依赖集成测试存在且受开关控制。

对齐 ci-matrix delta「依赖漏洞扫描与真实依赖集成测试」：
- test_db.py / test_speech.py 的既有契约断言 MUST 保留（不得用集成测试替代）；
- 数据库、语音服务 MUST 各有至少一条真实依赖路径的集成测试；
- 集成测试 MUST 可按开关启用（开关关闭时 skip，回归不红）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

COMMON_TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = COMMON_TESTS_DIR.parent.parent


def _read(name: str) -> str:
    return (COMMON_TESTS_DIR / name).read_text(encoding="utf-8")


class TestContractAssertionsRetained:
    def test_db_contract_file_keeps_structural_assertions(self):
        """test_db.py 保留 inspect 结构契约断言，且不引入环境开关（纯单测）。"""
        source = _read("test_db.py")
        assert "from sqlalchemy import inspect" in source
        assert "TestUserModel" in source and "TestLLMCallLogModel" in source

    def test_speech_contract_file_keeps_mock_provider_tests(self):
        """test_speech.py 保留 mock provider 契约断言。"""
        source = _read("test_speech.py")
        assert "ASRProvider" in source and "TTSProvider" in source
        assert "test_mock_recognize" in source


class TestRealDependencyIntegrationExists:
    def test_db_integration_uses_real_engine_with_gate(self):
        """数据库集成测试走真实引擎路径（create_async_engine），受 COMMON_DB_TESTS 开关控制。"""
        source = _read("test_db_integration.py")
        assert "COMMON_DB_TESTS" in source
        assert "create_async_engine" in source
        assert "Base.metadata.create_all" in source
        # 真实写入/读回/更新/删除全链路，而非仅 inspect 结构断言
        for step in ("session.add", "session.commit", "select(User", "sa_delete"):
            assert step in source

    def test_speech_integration_uses_real_provider_with_gate(self):
        """ASR 集成测试走真实 provider 路径，受 COMMON_ASR_TESTS 开关 + 非占位凭证双重门槛。"""
        source = _read("test_speech_integration.py")
        assert "COMMON_ASR_TESTS" in source
        assert "is_placeholder_secret" in source
        # 不打 mock 补丁，直接调用真实 provider
        assert "ASRProvider()" in source
        assert "monkeypatch" not in source


class TestGateBehavior:
    def test_integration_tests_skip_when_toggle_off(self):
        """开关关闭时两条集成测试 skip 且 pytest 退出码 0（回归不红）。"""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("COMMON_DB_TESTS", "COMMON_ASR_TESTS")
        }
        env["PYTEST_ADDOPTS"] = ""
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(COMMON_TESTS_DIR / "test_db_integration.py"),
                str(COMMON_TESTS_DIR / "test_speech_integration.py"),
                "-q",
                "-o",
                "addopts=-p no:cacheprovider --tb=short",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "skipped" in (proc.stdout + proc.stderr).lower()

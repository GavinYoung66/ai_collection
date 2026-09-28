"""Early pytest plugin: split rag+qc joint collection (duplicate ``tests`` packages).

Both services ship ``tests/__init__.py`` and ``from tests.*`` helpers, so a single
pytest process cannot load both ``tests.conftest`` modules (ImportPathMismatchError).
This plugin is registered via ``pytest.ini`` ``-p`` so it runs before those confests.

When the CLI asks for both ``services/rag-service/tests`` and
``services/qc-service/tests``, re-run each suite in its own subprocess and exit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_RAG_TESTS = "services/rag-service/tests"
_QC_TESTS = "services/qc-service/tests"


def _is_tests_path(arg: str, needle: str) -> bool:
    norm = str(arg).replace("\\", "/").rstrip("/")
    return norm == needle or norm.endswith("/" + needle)


def pytest_load_initial_conftests(early_config, parser, args):
    has_rag = any(_is_tests_path(a, _RAG_TESTS) for a in args)
    has_qc = any(_is_tests_path(a, _QC_TESTS) for a in args)
    if not (has_rag and has_qc):
        return

    passthrough = [
        a
        for a in list(args)
        if not _is_tests_path(a, _RAG_TESTS) and not _is_tests_path(a, _QC_TESTS)
    ]
    base = [sys.executable, "-m", "pytest", *passthrough]
    exit_code = 0
    for path in (_RAG_TESTS, _QC_TESTS):
        cmd = [*base, path]
        print(f"[rag+qc split] running: {' '.join(cmd)}", flush=True)
        rc = subprocess.call(cmd, cwd=str(_ROOT))
        if rc != 0:
            exit_code = rc
    # Stop this process before conflicting service confests are imported.
    sys.exit(exit_code)

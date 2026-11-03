"""T20 依赖漏洞整改契约测试。

覆盖三类约束：
1. 正常流程：全局清单钉死已修复版本，JWT 签发/验签在 PyJWT 下行为不变；
2. 边界场景：算法白名单只接受配置算法，跨算法 token 一律拒绝；
3. 异常场景：过期/伪造签名 token 被拒，且全仓不再残留 python-jose 依赖。
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest

from common.config.settings import settings
from common.security.rbac import create_token, verify_token, verify_token_with_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS = ("requirements.in", "requirements.lock", "requirements.txt")
PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s#]+)")
# 已修复版本的单一事实源（T20 验收口径）
_REMEDIATED_PINS = {
    "aiohttp": "3.14.5",
    "python-multipart": "0.0.33",
    "fastapi": "0.109.1",
    "cryptography": "50.0.2",
    "pillow": "13.0.0",
    "langsmith": "0.8.18",
    "pyjwt": "2.15.1",
}
_EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    "openspec",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
}


def _pins(filename: str) -> dict[str, str]:
    """解析清单一中 `name==version` 钉死条目（忽略注释与区间约束）。"""
    pins: dict[str, str] = {}
    for line in (REPO_ROOT / filename).read_text(encoding="utf-8").splitlines():
        match = PIN_RE.match(line.strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _jose_importers() -> list[str]:
    """AST 扫描全仓 Python 源码，返回仍导入 jose 的文件（字符串字面量不误报）。"""
    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(m == "jose" or m.startswith("jose.") for m in modules):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    return offenders


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_manifest_pins_remediated_versions(manifest):
    pins = _pins(manifest)
    for name, version in _REMEDIATED_PINS.items():
        assert pins.get(name) == version, f"{manifest} 未钉 {name}=={version}"


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_manifest_has_no_jose_family_pins(manifest):
    pins = _pins(manifest)
    # ecdsa/rsa/pyasn1 是 python-jose 的传递依赖，随迁移一并移除
    for name in ("python-jose", "ecdsa", "rsa", "pyasn1"):
        assert name not in pins, f"{manifest} 仍钉死已移除依赖 {name}"


def test_no_source_imports_jose():
    offenders = _jose_importers()
    assert offenders == [], f"仍有源码导入 jose：{offenders}"


def test_ci_runs_dependency_audit_gate():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "dependency-audit:" in ci
    assert "pip-audit" in ci
    assert "requirements.lock" in ci
    assert "--no-deps" in ci
    # 例外必须有登记与复查日期，否则视为静默忽略
    assert "PYSEC-2026-1845" in ci
    assert "2027-05-01" in ci


def test_jwt_roundtrip_normal(jwt_secret):
    token = create_token({"sub": "user-t20", "role": "agent"})
    payload = verify_token(token)
    assert payload is not None
    assert payload["sub"] == "user-t20"
    assert payload["role"] == "agent"
    assert "exp" in payload


def test_expired_token_rejected(jwt_secret):
    expired = pyjwt.encode(
        {"sub": "user-t20", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert verify_token(expired) is None
    payload, reason = verify_token_with_reason(expired)
    assert payload is None
    assert reason == "token expired"


def test_forged_signature_rejected(jwt_secret):
    forged = pyjwt.encode(
        {"sub": "user-t20"},
        "wrong_secret_key_0123456789abcdef",
        algorithm=settings.JWT_ALGORITHM,
    )
    assert verify_token(forged) is None
    payload, reason = verify_token_with_reason(forged)
    assert payload is None
    assert reason == "invalid signature"


def test_algorithm_confusion_rejected(monkeypatch):
    """边界：算法白名单外（如 HS512）的 token 必须拒绝，不随密钥有效而放行。"""
    # 用同一密钥签 HS512（64 字节满足该算法建议长度），仅算法不同 → 必须因白名单拒绝
    same_secret = "test_jwt_secret_key_0123456789abcdef" + "0" * 28
    monkeypatch.setattr(settings, "JWT_SECRET", same_secret)
    confused = pyjwt.encode(
        {"sub": "user-t20", "role": "admin"},
        same_secret,
        algorithm="HS512",
    )
    assert verify_token(confused) is None


def test_non_jwt_garbage_rejected():
    assert verify_token("not-a-jwt") is None

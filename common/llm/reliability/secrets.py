"""占位密钥校验（单一职责：fail-closed 的真路径密钥断言）。

已知占位符/空密钥一律视为未配置；真路径调用 ``require_real_secret``
在占位或缺失时抛错，绝不让占位密钥静默进入外部请求。
"""
from __future__ import annotations

PLACEHOLDER_SECRETS = frozenset(
    {"", "change_me", "<change_me>", "dev_cot_key_stub_change_me", "placeholder", "<placeholder>"}
)


def is_placeholder_secret(value: str | None) -> bool:
    """已知占位符密钥视为未配置。"""
    return (value or "").strip() in PLACEHOLDER_SECRETS


def require_real_secret(value: str | None, *, label: str = "API key") -> str:
    """真路径：占位或空则 fail-closed。"""
    text = (value or "").strip()
    if is_placeholder_secret(text):
        raise RuntimeError(f"{label} is missing or placeholder (change_me)")
    return text

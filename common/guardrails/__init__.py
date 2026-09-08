"""共享 Guardrails 最小钩子：前置/后置文本检查。"""
from __future__ import annotations

from dataclasses import dataclass, field

from common.security.sensitive_words import SensitiveWordMatcher, sensitive_matcher


@dataclass
class GuardrailResult:
    ok: bool
    hits: list[str] = field(default_factory=list)
    reason: str = ""


def _scan(text: str, words: list[str] | None, matcher: SensitiveWordMatcher | None) -> list[str]:
    if not text:
        return []
    if words is not None:
        m = SensitiveWordMatcher()
        m.load_words(list(words))
        return [h["word"] for h in m.match(text)]
    use = matcher or sensitive_matcher
    return [h["word"] for h in use.match(text)]


def precheck_text(
    text: str,
    *,
    words: list[str] | None = None,
    matcher: SensitiveWordMatcher | None = None,
) -> GuardrailResult:
    """前置：命中阻塞词则 ok=False。"""
    hits = _scan(text, words, matcher)
    if hits:
        return GuardrailResult(ok=False, hits=hits, reason="precheck_blocked")
    return GuardrailResult(ok=True)


def postcheck_text(
    text: str,
    *,
    words: list[str] | None = None,
    matcher: SensitiveWordMatcher | None = None,
) -> GuardrailResult:
    """后置：命中红线词则 ok=False。"""
    hits = _scan(text, words, matcher)
    if hits:
        return GuardrailResult(ok=False, hits=hits, reason="postcheck_blocked")
    return GuardrailResult(ok=True)

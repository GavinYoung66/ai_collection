"""共享 Guardrails 最小钩子：前置/后置文本检查。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from common.security.sensitive_words import SensitiveWordMatcher, sensitive_matcher

# T124：words= 路径 AC 实例内容缓存，键为词库排序后 tuple（顺序无关）。
# 命中不重建，热路径重复调用直接复用；上限显式常量、满即清空防膨胀；
# 锁保护并发读写。matcher 构建后只读（automaton 不可变），跨线程共享安全。
_WORDS_MATCHER_CACHE: dict[tuple[str, ...], SensitiveWordMatcher] = {}
_WORDS_MATCHER_CACHE_LOCK = threading.Lock()
_WORDS_MATCHER_CACHE_MAX = 32


def _get_matcher_for_words(words: list[str]) -> SensitiveWordMatcher:
    """按词库内容取缓存的 AC 实例，未命中才构建（构建在锁内，同键并发仅一次）。"""
    key = tuple(sorted(words))
    with _WORDS_MATCHER_CACHE_LOCK:
        matcher = _WORDS_MATCHER_CACHE.get(key)
        if matcher is None:
            matcher = SensitiveWordMatcher()
            matcher.load_words(list(words))
            if len(_WORDS_MATCHER_CACHE) >= _WORDS_MATCHER_CACHE_MAX:
                _WORDS_MATCHER_CACHE.clear()
            _WORDS_MATCHER_CACHE[key] = matcher
        return matcher


@dataclass
class GuardrailResult:
    ok: bool
    hits: list[str] = field(default_factory=list)
    reason: str = ""


def _scan(text: str, words: list[str] | None, matcher: SensitiveWordMatcher | None) -> list[str]:
    if not text:
        return []
    if words is not None:
        m = _get_matcher_for_words(words)
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

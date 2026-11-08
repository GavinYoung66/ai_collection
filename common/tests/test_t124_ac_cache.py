"""T124：AC 自动机 words= 路径缓存复用 + 未构建 matcher 安全性测试。"""
from __future__ import annotations

import threading

import pytest

import common.guardrails as guardrails
from common.guardrails import postcheck_text, precheck_text
from common.security.sensitive_words import SensitiveWordMatcher, sensitive_matcher


@pytest.fixture(autouse=True)
def _clear_words_cache():
    """每个用例前后清空 words= 路径缓存，保证用例间隔离。"""
    guardrails._WORDS_MATCHER_CACHE.clear()
    yield
    guardrails._WORDS_MATCHER_CACHE.clear()


def test_words_path_reuses_same_matcher_instance():
    """同词库内容复用同一 matcher 实例（顺序无关）。"""
    m1 = guardrails._get_matcher_for_words(["弄死", "转账"])
    m2 = guardrails._get_matcher_for_words(["弄死", "转账"])
    assert m1 is m2
    # 排序后键相同：词库顺序不同仍命中同一缓存
    m3 = guardrails._get_matcher_for_words(["转账", "弄死"])
    assert m3 is m1


def test_words_path_builds_once_for_repeated_calls(monkeypatch):
    """热路径重复 precheck 调用仅构建一次（load_words 计数断言）。"""
    counter = {"n": 0}
    real_load = SensitiveWordMatcher.load_words

    def counting_load(self, words):
        counter["n"] += 1
        return real_load(self, words)

    monkeypatch.setattr(SensitiveWordMatcher, "load_words", counting_load)
    for _ in range(3):
        r = precheck_text("你好弄死他", words=["弄死", "我是法院"])
        assert r.ok is False
        assert "弄死" in r.hits
    assert counter["n"] == 1


def test_different_words_get_different_instances():
    """不同词库得到不同缓存实例，互不串扰。"""
    m1 = guardrails._get_matcher_for_words(["词甲"])
    m2 = guardrails._get_matcher_for_words(["词乙"])
    assert m1 is not m2
    assert len(guardrails._WORDS_MATCHER_CACHE) == 2
    assert precheck_text("包含词甲的文本", words=["词甲"]).ok is False
    assert precheck_text("包含词甲的文本", words=["词乙"]).ok is True


def test_cache_capacity_bounded():
    """超过容量上限后缓存清空重建，总量不超过显式常量上限。"""
    for i in range(guardrails._WORDS_MATCHER_CACHE_MAX + 3):
        guardrails._get_matcher_for_words([f"膨胀词{i}"])
    assert len(guardrails._WORDS_MATCHER_CACHE) <= guardrails._WORDS_MATCHER_CACHE_MAX


def test_uninitialized_matcher_match_returns_empty():
    """未 load_words 的 matcher：match 返回 []，不抛异常。"""
    m = SensitiveWordMatcher()
    assert m.match("任意文本弄死") == []


def test_uninitialized_matcher_contains_false():
    """未构建 matcher：contains 返回 False，不抛异常。"""
    m = SensitiveWordMatcher()
    assert m.contains("任意文本弄死") is False


def test_empty_words_load_returns_empty():
    """空词库 load 后 match/contains 仍安全返回空结果。

    实测 pyahocorasick：空 trie 调 make_automaton() 成功但 kind 仍为
    EMPTY(0)，iter() 会抛 AttributeError，kind 兜底分支恰好覆盖。
    """
    m = SensitiveWordMatcher()
    m.load_words([])
    assert m.match("弄死他") == []
    assert m.contains("弄死他") is False


def test_module_level_matcher_safe_without_init():
    """模块级 sensitive_matcher 从未 load_words：match/contains 不抛异常。"""
    assert sensitive_matcher.match("hello world") == []
    assert sensitive_matcher.contains("hello world") is False


def test_default_matcher_path_returns_ok():
    """默认路径（words=None、无 matcher）走未构建模块级实例：不命中、ok=True。"""
    r = precheck_text("正常业务文本")
    assert r.ok is True and r.hits == []
    r2 = postcheck_text("正常业务文本")
    assert r2.ok is True and r2.hits == []


def test_precheck_postcheck_words_regression():
    """既有语义回归：words 命中阻塞、未命中放行。"""
    r = precheck_text("你好弄死他", words=["弄死"])
    assert r.ok is False and "弄死" in r.hits
    r2 = postcheck_text("经核实情况说明如下", words=["弄死", "我是法院"])
    assert r2.ok is True and r2.hits == []


def test_concurrent_words_scan_thread_safe():
    """多线程并发 words= 扫描：结果正确、无异常（锁保护缓存读写）。"""
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(20):
                r = precheck_text("你好弄死他", words=["弄死"])
                assert r.ok is False
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
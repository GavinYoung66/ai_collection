"""common.guardrails 单测。"""
from common.guardrails import postcheck_text, precheck_text


def test_precheck_blocks_word():
    r = precheck_text("你好弄死他", words=["弄死"])
    assert r.ok is False
    assert "弄死" in r.hits


def test_postcheck_ok_clean():
    r = postcheck_text("经核实情况说明如下", words=["弄死", "我是法院"])
    assert r.ok is True
    assert r.hits == []

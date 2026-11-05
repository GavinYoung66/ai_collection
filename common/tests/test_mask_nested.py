"""T49 / 2.2：递归脱敏 mask_nested + _mask_processor 嵌套覆盖 单测。

验收判据：
- 顶层字符串字段掩码行为回归不变（兼容 test_config.py / test_t19 口径）；
- 嵌套 dict/list/tuple 内字符串叶子（含 11 位手机号）被掩码；
- 深度上限 8：超限容器原样返回；
- 循环引用：id 集合防重入，不栈溢出；
- mask_text 异常 best-effort 返回原值（递归内部不抛）；
- 非 str 标量（int/None/datetime）原样返回。
"""
from __future__ import annotations

from datetime import datetime


from common.config.logging import _MASK_DEPTH_LIMIT, _mask_processor, mask_nested


class TestMaskNested:
    def test_top_level_string_masked(self):
        result = mask_nested("联系客户 13812341234 请尽快还款")
        assert "13812341234" not in result
        assert "138****1234" in result

    def test_nested_dict_phone_masked(self):
        entry = {
            "tool": "crm.query",
            "params": {"arguments": {"phone": "13812341234", "name": "ok"}},
        }
        result = mask_nested(entry)
        assert "13812341234" not in str(result)
        assert "138****1234" in result["params"]["arguments"]["phone"]

    def test_nested_list_phone_masked(self):
        result = mask_nested({"args": ["13812341234", "plain"]})
        assert result["args"][0] == "138****1234"
        assert result["args"][1] == "plain"

    def test_tuple_preserved_with_masking(self):
        result = mask_nested(("13812341234", "x"))
        assert isinstance(result, tuple)
        assert result[0] == "138****1234"

    def test_deep_nesting_beyond_limit_returned_as_is(self):
        value: dict = {"leaf": "13812341234"}
        root = value
        for _ in range(_MASK_DEPTH_LIMIT + 4):  # 深度明显超限
            root["next"] = {}
            root = root["next"]
        root["leaf"] = "13812341234"
        result = mask_nested(value)
        assert "13812341234" in str(result), "超限深度的内容应原样返回（不下钻）"

    def test_depth_limit_boundary_still_masked(self):
        """恰好处于上限内的嵌套（depth ≤ 8）仍被下钻掩码。"""
        value: dict = {"leaf": "13812341234"}
        root = value
        for _ in range(_MASK_DEPTH_LIMIT - 1):
            root["next"] = {}
            root = root["next"]
        root["leaf"] = "13812341234"
        result = mask_nested(value)
        assert "13812341234" not in str(result)

    def test_circular_reference_no_stack_overflow(self):
        loop: dict = {}
        loop["self"] = loop
        loop["phone"] = "13812341234"
        result = mask_nested(loop)  # 不抛 / 不栈溢出即为通过
        assert result["phone"] == "138****1234"

    def test_circular_reference_truncated_to_placeholder_no_plaintext(self):
        """W2：循环引用命中截断为占位符，原对象与明文子树不进入结果。"""
        loop: dict = {"phone": "13812341234"}
        loop["self"] = loop
        result = mask_nested(loop)
        assert result["self"] == "[circular]"
        assert result["phone"] == "138****1234"
        assert "13812341234" not in str(result)

    def test_set_elements_masked(self):
        """W2：set 元素中的字符串叶子被掩码（原实现不下钻 set）。"""
        result = mask_nested({"tags": {"13812341234", "plain"}})
        masked = result["tags"]
        assert isinstance(masked, set)
        assert "13812341234" not in masked
        assert "138****1234" in masked
        assert "plain" in masked

    def test_frozenset_elements_masked_preserving_type(self):
        result = mask_nested({"ids": frozenset({"15912341234"})})
        masked = result["ids"]
        assert isinstance(masked, frozenset)
        assert "159****1234" in masked

    def test_bytes_leaf_masked(self):
        """W2：bytes 叶子解码后按 str 掩码（原实现原样返回漏掩）。"""
        result = mask_nested({"blob": b"call 13812341234 now"})
        assert "13812341234" not in str(result["blob"])
        assert "138****1234" in result["blob"]

    def test_dict_string_key_masked(self):
        """W2：dict 的 str key 亦过掩码（key 本身可能携带 PII）。"""
        result = mask_nested({"13812341234": "value"})
        assert "13812341234" not in result
        assert "138****1234" in result

    def test_shared_acyclic_reference_masked_everywhere(self):
        """W2：非循环的共享引用（DAG）不被误判为循环，两处均完整掩码。"""
        shared = {"phone": "13812341234"}
        result = mask_nested({"a": shared, "b": shared})
        assert result["a"]["phone"] == "138****1234"
        assert result["b"]["phone"] == "138****1234"

    def test_mask_text_failure_best_effort_returns_original(self, monkeypatch):
        """mask_text 异常：best-effort 返回原值，递归内部不抛。

        patch 目标为 ``common.config.logging`` 命名空间（``from ... import``
        已在该模块绑定函数对象，patch mask 模块不影响此处引用）。
        """
        monkeypatch.setattr(
            "common.config.logging.mask_text",
            lambda text: (_ for _ in ()).throw(RuntimeError("mask down")),
        )
        assert mask_nested("13812341234") == "13812341234"

    def test_non_string_scalars_untouched(self):
        now = datetime(2026, 11, 5, 12, 0, 0)
        result = mask_nested({"count": 42, "data": None, "at": now, "ratio": 0.5})
        assert result == {"count": 42, "data": None, "at": now, "ratio": 0.5}


class TestMaskProcessorNestedCoverage:
    def test_processor_masks_nested_params(self):
        """structlog event_dict 的嵌套容器（mcp-gateway 审计 params 形态）被覆盖。"""
        event_dict = {
            "event": "mcp_call_audit",
            "params": {"arguments": {"mobile": "13812341234"}},
            "ids": ["15912341234"],
        }
        result = _mask_processor(None, None, event_dict)
        assert result["params"]["arguments"]["mobile"] == "138****1234"
        assert result["ids"][0] == "159****1234"

    def test_processor_top_level_regression(self, monkeypatch):
        """顶层字符串掩码行为与改造前一致（既有用例同口径）。"""
        event_dict = {"message": "联系客户 13812341234 请尽快还款"}
        result = _mask_processor(None, None, event_dict)
        assert "138****1234" in result["message"]
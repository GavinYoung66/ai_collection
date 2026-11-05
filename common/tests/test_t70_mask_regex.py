"""T70 / 7.4：脱敏正则补齐（拼接手机号/15 位身份证/银行卡 Luhn）单测。

验收判据：
- ``138001380008001``（拼接手机号 15 位）被整体掩码；
- 15 位老身份证被掩码；
- ``4111111111111111``（Luhn 通过）被掩码；
- 普通订单号不误掩；Luhn 校验失败的长数字不掩码；
- 既有回归：11 位手机号 / 18 位身份证掩码形态不变。
"""
from __future__ import annotations

from common.security.mask import mask_text


class TestMaskRegexT70:
    def test_concatenated_phone_masked(self):
        """拼接手机号（15 位）整体掩码：138****8001。"""
        result = mask_text("订单号 138001380008001 请查收")
        assert "138001380008001" not in result
        assert "138****8001" in result

    def test_15_digit_id_card_masked(self):
        """15 位老身份证：前 6 后 3 掩码。"""
        result = mask_text("身份证 110101001001001 信息")
        assert "110101001001001" not in result
        assert "110101" in result

    def test_bank_card_luhn_pass_masked(self):
        """4111111111111111：Luhn 通过 → 掩码。"""
        result = mask_text("卡号 4111111111111111")
        assert "4111111111111111" not in result

    def test_luhn_fail_long_number_not_masked(self):
        """Luhn 校验失败的长数字：不掩码（降低订单号误伤）。"""
        raw = "1234567890123456"
        assert raw in mask_text(f"流水 {raw}")

    def test_normal_order_number_not_masked(self):
        """14 位普通订单号：不命中任何规则。"""
        order = "20261105000123"
        assert order in mask_text(f"订单 {order}")

    def test_regression_phone_11(self):
        """11 位手机号掩码形态不变（回归）。"""
        assert "138****1234" in mask_text("联系 13812341234")

    def test_regression_id_18(self):
        """18 位身份证掩码形态不变（回归）。"""
        result = mask_text("证号 110101199001011234")
        assert "110101" in result
        assert "199001011234" not in result

    def test_longer_than_phone_plus_suffix_not_matched(self):
        """超过拼接上限（>15 位且 1[3-9] 开头）：不误拆整体（边界回溯验证）。"""
        raw = "13800138000800123456"  # 20 位
        assert raw in mask_text(f"编号 {raw}")
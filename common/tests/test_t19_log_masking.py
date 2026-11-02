"""T19 日志脱敏覆盖面单测。

验证 structlog 脱敏处理器复用 ``common.security.mask.mask_text``：
手机号、身份证号、银行卡号在日志文本中均为掩码形式，明文不落日志。
"""
from __future__ import annotations

from common.config.logging import _mask_processor
from common.security.mask import mask_text


def _process(**fields) -> dict:
    return _mask_processor(None, None, dict(fields))


class TestLogMasking:
    def test_phone_is_masked(self):
        result = _process(message="联系客户 13812341234 尽快处理")
        assert "13812341234" not in result["message"]
        assert "138****1234" in result["message"]

    def test_id_card_is_masked(self):
        result = _process(message="身份证 110101199001011234")
        assert "110101199001011234" not in result["message"]
        assert "110101********1234" in result["message"]

    def test_bank_card_16_digits_is_masked(self):
        result = _process(message="卡号 6222021234567890")
        assert "6222021234567890" not in result["message"]
        assert "**** **** **** 7890" in result["message"]

    def test_bank_card_19_digits_is_masked(self):
        result = _process(message="卡号 6222021234567890123")
        assert "6222021234567890123" not in result["message"]
        assert "**** **** **** 0123" in result["message"]

    def test_multiple_pii_types_in_one_message(self):
        raw = "客户 13812341234，身份证 110101199001011234，卡号 6222021234567890"
        result = _process(message=raw)
        assert "13812341234" not in result["message"]
        assert "110101199001011234" not in result["message"]
        assert "6222021234567890" not in result["message"]
        assert result["message"].count("*") >= 20

    def test_every_string_field_is_masked(self):
        result = _process(
            message="身份证 110101199001011234",
            detail="卡号 6222021234567890",
            count=42,
        )
        assert "110101199001011234" not in result["detail"] + result["message"]
        assert result["count"] == 42

    def test_non_string_values_are_untouched(self):
        result = _process(count=42, data=None, flag=True, payload={"k": "v"})
        assert result["count"] == 42
        assert result["data"] is None
        assert result["flag"] is True
        assert result["payload"] == {"k": "v"}

    def test_short_digit_run_is_not_masked(self):
        result = _process(message="单号 123456789012345")
        assert result["message"] == "单号 123456789012345"

    def test_matches_shared_mask_text_contract(self):
        raw = "13812341234 / 110101199001011234 / 6222021234567890"
        assert _process(message=raw)["message"] == mask_text(raw)

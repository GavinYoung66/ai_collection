"""common.security 模块单元测试。"""
import pytest

from common.security.crypto import decrypt, encrypt
from common.security.mask import mask_bank_card, mask_id_card, mask_name, mask_phone, mask_text
from common.security.rbac import create_token, verify_token
from common.security.sensitive_words import SensitiveWordMatcher


class TestCrypto:
    def test_encrypt_decrypt_roundtrip(self, aes_key):
        plaintext = "13812341234"
        encrypted = encrypt(plaintext)
        assert encrypted != plaintext
        assert decrypt(encrypted) == plaintext

    def test_encrypt_different_each_time(self, aes_key):
        text = "敏感数据"
        enc1 = encrypt(text)
        enc2 = encrypt(text)
        assert enc1 != enc2
        assert decrypt(enc1) == text
        assert decrypt(enc2) == text

    def test_encrypt_empty_string(self, aes_key):
        enc = encrypt("")
        assert decrypt(enc) == ""


class TestMask:
    def test_mask_phone(self):
        assert mask_phone("13812341234") == "138****1234"
        assert mask_phone("13812341234") != "13812341234"

    def test_mask_phone_short(self):
        assert mask_phone("123") == "123"

    def test_mask_id_card(self):
        masked = mask_id_card("110101199001011234")
        assert masked.startswith("110101")
        assert masked.endswith("1234")
        assert "********" in masked

    def test_mask_id_card_short(self):
        assert mask_id_card("123") == "123"

    def test_mask_bank_card(self):
        masked = mask_bank_card("6225756123456789")
        assert masked.endswith("6789")
        assert masked.startswith("****")

    def test_mask_bank_card_short(self):
        assert mask_bank_card("123") == "123"

    def test_mask_name(self):
        assert mask_name("张三") == "张*"
        assert mask_name("欧阳修") == "欧**"
        assert mask_name("王") == "王"

    def test_mask_text_multiple_types(self):
        text = "手机13812341234身份证110101199001011234卡6225756123456789"
        masked = mask_text(text)
        assert "13812341234" not in masked
        assert "110101199001011234" not in masked
        assert "6225756123456789" not in masked
        assert "138****1234" in masked


class TestRBAC:
    def test_create_and_verify_token(self):
        token = create_token({"sub": "user123", "role": "agent"})
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["role"] == "agent"

    def test_verify_invalid_token(self):
        result = verify_token("invalid_token_string")
        assert result is None

    def test_token_has_expiry(self):
        token = create_token({"sub": "user123"})
        payload = verify_token(token)
        assert payload is not None
        assert "exp" in payload

    def test_token_custom_expiry(self):
        token = create_token({"sub": "user123"}, expires_hours=1)
        payload = verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"


class TestSensitiveWords:
    @pytest.fixture()
    def matcher(self):
        m = SensitiveWordMatcher()
        m.load_words(["上门催收", "影响征信", "辱骂", "不用还了"])
        return m

    def test_load_and_match(self, matcher):
        hits = matcher.match("如果不还就上门催收")
        assert len(hits) == 1
        assert hits[0]["word"] == "上门催收"

    def test_no_match(self, matcher):
        hits = matcher.match("正常催收话术")
        assert len(hits) == 0

    def test_contains_true(self, matcher):
        assert matcher.contains("逾期不还会影响征信") is True

    def test_contains_false(self, matcher):
        assert matcher.contains("请尽快还款") is False

    def test_multiple_matches(self, matcher):
        hits = matcher.match("上门催收并且影响征信")
        assert len(hits) == 2

    def test_empty_text(self, matcher):
        assert matcher.match("") == []
        assert matcher.contains("") is False

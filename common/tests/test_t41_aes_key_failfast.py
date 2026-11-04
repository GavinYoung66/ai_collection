"""T41：AES 密钥启动 fail-fast 格式校验。

覆盖风险：默认 `change_me` 非法 hex 延迟到首次加解密才抛错（运行时故障）。
整改后：占位值与格式非法值均在启动校验（validate_required_secrets）即失败。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from common.config.settings import validate_required_secrets
from common.security.crypto import AES_KEY_BYTES_LEN, aes_key_bytes, decrypt, encrypt
from common.security.crypto import _get_key

VALID_HEX = "0123456789abcdef" * 4  # 64 位 hex = 32 字节


def _cfg(aes_key: str, jwt_secret: str = "jwt_secret_long_enough_0123456789"):
    return SimpleNamespace(AES_KEY=aes_key, JWT_SECRET=jwt_secret)


class TestValidateRequiredSecretsFailFast:
    """启动校验：占位/非法格式立即失败，合法值放行。"""

    def test_placeholder_aes_key_fails_at_startup(self):
        """异常场景：默认占位值 change_me → 启动 RuntimeError（占位语义）。"""
        with pytest.raises(RuntimeError, match="占位值"):
            validate_required_secrets(_cfg("change_me"))

    def test_empty_aes_key_fails_at_startup(self):
        """边界场景：空字符串同样按占位值拒绝。"""
        with pytest.raises(RuntimeError, match="占位值"):
            validate_required_secrets(_cfg(""))

    def test_invalid_hex_aes_key_fails_at_startup(self):
        """异常场景：非占位但非法 hex（原缺陷：要到首次 encrypt/decrypt 才抛 ValueError）。"""
        with pytest.raises(RuntimeError, match="格式非法"):
            validate_required_secrets(_cfg("not-a-hex-string!!"))

    def test_wrong_length_aes_key_fails_at_startup(self):
        """边界场景：合法 hex 但长度非 AES-256（16 字节）→ 启动即失败。"""
        short = "0123456789abcdef"  # 8 字节
        with pytest.raises(RuntimeError, match="长度非法"):
            validate_required_secrets(_cfg(short))
        with pytest.raises(RuntimeError, match="长度非法"):
            validate_required_secrets(_cfg("ab" * 16))  # 16 字节

    def test_valid_aes_key_passes(self, aes_key):
        """正常流程：conftest 提供的 32 字节随机 hex 密钥放行，不抛异常。"""
        validate_required_secrets(_cfg(aes_key))

    def test_startup_error_does_not_leak_key(self):
        """异常场景：失败信息只报格式问题，不回显密钥内容。"""
        secret_like = "deadbeef" * 7 + "de"  # 62 位 hex（31 字节，长度非法）
        with pytest.raises(RuntimeError) as exc_info:
            validate_required_secrets(_cfg(secret_like))
        assert secret_like not in str(exc_info.value)


class TestAesKeyBytesSource:
    """crypto.aes_key_bytes：格式校验单一事实源。"""

    def test_valid_key_returns_bytes(self):
        """正常流程：64 位 hex → 32 字节。"""
        assert aes_key_bytes(VALID_HEX) == bytes.fromhex(VALID_HEX)
        assert len(aes_key_bytes(VALID_HEX)) == AES_KEY_BYTES_LEN

    def test_uppercase_hex_accepted(self):
        """边界场景：大写 hex 等价（bytes.fromhex 语义）。"""
        assert aes_key_bytes(VALID_HEX.upper()) == bytes.fromhex(VALID_HEX)

    def test_surrounding_whitespace_stripped(self):
        """边界场景：首尾空白容忍（与配置注入习惯一致），内容仍须合法。"""
        assert aes_key_bytes(f"  {VALID_HEX}\n") == bytes.fromhex(VALID_HEX)

    @pytest.mark.parametrize("bad", ["", "zz" * 32, "0" * 63, "change_me"])
    def test_invalid_inputs_raise_value_error(self, bad):
        """异常场景：空/非 hex/奇数位/占位串均抛 ValueError。"""
        with pytest.raises(ValueError):
            aes_key_bytes(bad)

    def test_get_key_uses_validated_source(self, aes_key):
        """正常流程：_get_key 经校验返回密钥字节。"""
        assert _get_key() == bytes.fromhex(aes_key)


class TestEncryptDecryptRegression:
    """加解密回归：校验前置不改变正常加解密行为。"""

    def test_roundtrip_with_valid_key(self, aes_key):
        """正常流程：合法密钥加解密往返一致。"""
        plaintext = "13800138000|张三"
        assert decrypt(encrypt(plaintext)) == plaintext

    def test_roundtrip_unicode_boundary(self, aes_key):
        """边界场景：空串与多字节 Unicode 均可往返。"""
        assert decrypt(encrypt("")) == ""
        assert decrypt(encrypt("😀\u00a5¥")) == "😀\u00a5¥"

"""AES-256-GCM 加密/解密。"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.config.settings import settings

# AES-256 密钥长度（字节）；十六进制字符串须恰为 2×32=64 位。
AES_KEY_BYTES_LEN = 32


def aes_key_bytes(key: str) -> bytes:
    """校验 AES 密钥格式并返回密钥字节（T41 格式校验单一事实源）。

    fail-fast：非法 hex / 长度不符（非 AES-256）立即抛 ValueError，
    错误信息不回显密钥内容。启动路径经
    `common.config.settings.validate_required_secrets` 调用本函数，
    避免延迟到首次加解密才报错。
    """
    stripped = (key or "").strip()
    try:
        raw = bytes.fromhex(stripped)
    except ValueError as exc:
        raise ValueError(
            f"AES_KEY 格式非法：不是合法的十六进制字符串（期望 {AES_KEY_BYTES_LEN * 2} 位 hex）"
        ) from exc
    if len(raw) != AES_KEY_BYTES_LEN:
        raise ValueError(
            f"AES_KEY 长度非法：期望 {AES_KEY_BYTES_LEN} 字节（{AES_KEY_BYTES_LEN * 2} 位 hex），"
            f"实际 {len(raw)} 字节"
        )
    return raw


def _get_key() -> bytes:
    return aes_key_bytes(settings.AES_KEY)


def encrypt(plaintext: str) -> str:
    """加密明文，返回 base64(nonce + ciphertext)。"""
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("utf-8")


def decrypt(encrypted: str) -> str:
    """解密 base64(nonce + ciphertext)。"""
    key = _get_key()
    aesgcm = AESGCM(key)
    data = base64.b64decode(encrypted)
    nonce, ciphertext = data[:12], data[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")

"""AES-256-GCM 加密/解密。"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.config.settings import settings


def _get_key() -> bytes:
    return bytes.fromhex(settings.AES_KEY)


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

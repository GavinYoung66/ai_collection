"""数据脱敏工具。"""
import re

_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_ID_CARD_RE = re.compile(r"\d{18}")
_BANK_CARD_RE = re.compile(r"\d{16,19}")


def mask_phone(phone: str) -> str:
    """手机号脱敏：138****1234。"""
    if len(phone) >= 11:
        return phone[:3] + "****" + phone[-4:]
    return phone


def mask_id_card(id_card: str) -> str:
    """身份证脱敏：110101********1234。"""
    if len(id_card) >= 18:
        return id_card[:6] + "********" + id_card[-4:]
    return id_card


def mask_bank_card(card: str) -> str:
    """银行卡脱敏：**** **** **** 1234。"""
    if len(card) >= 4:
        return "**** **** **** " + card[-4:]
    return card


def mask_name(name: str) -> str:
    """姓名脱敏：张**。"""
    if len(name) >= 2:
        return name[0] + "*" * (len(name) - 1)
    return name


def mask_text(text: str) -> str:
    """通用脱敏：对文本中所有手机号/身份证/银行卡进行脱敏。"""
    text = _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)
    text = _ID_CARD_RE.sub(lambda m: mask_id_card(m.group()), text)
    text = _BANK_CARD_RE.sub(lambda m: mask_bank_card(m.group()), text)
    return text

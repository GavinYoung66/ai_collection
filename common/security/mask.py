"""数据脱敏工具。"""
import re

# 数字边界（T19）：避免手机号正则命中更长数字串的内部片段。
# T70：放宽为允许后缀 1-4 位整体掩码（拼接手机号场景，如 138001380008001）；
# 更长数字串因 (?!\d) 边界回溯整体不命中，不误拆。
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?:\d{1,4})?(?!\d)")
# 身份证：18 位，末位可为 X（校验位）
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
# T70：15 位老身份证（置于 18 位规则后，保持"先长后短"防截断误判）
_ID_CARD_15_RE = re.compile(r"(?<!\d)\d{15}(?!\d)")
# T70：银行卡 15（Amex）/16–19 位全覆盖；命中后须通过 Luhn 校验才掩码，
# 降低订单号/流水号误伤。
_BANK_CARD_RE = re.compile(r"(?<!\d)\d{15,19}(?!\d)")


def _luhn_ok(number: str) -> bool:
    """Luhn 校验（模 10）：银行卡号命中后的二次确认。"""
    digits = [int(d) for d in number]
    parity = len(digits) % 2
    checksum = 0
    for i, digit in enumerate(digits):
        if i % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def mask_phone(phone: str) -> str:
    """手机号脱敏：138****1234（含拼接后缀时整体掩码，T70）。"""
    if len(phone) >= 11:
        return phone[:3] + "****" + phone[-4:]
    return phone


def mask_id_card(id_card: str) -> str:
    """身份证脱敏：110101********1234（18 位）；15 位老证前 6 后 3（T70）。"""
    if len(id_card) >= 18:
        return id_card[:6] + "********" + id_card[-4:]
    if len(id_card) >= 15:
        return id_card[:6] + "********" + id_card[-3:]
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
    """通用脱敏：手机号（含拼接）/身份证（18+15 位）/银行卡（Luhn 校验）。"""
    text = _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)
    text = _ID_CARD_RE.sub(lambda m: mask_id_card(m.group()), text)
    text = _ID_CARD_15_RE.sub(lambda m: mask_id_card(m.group()), text)
    text = _BANK_CARD_RE.sub(
        lambda m: mask_bank_card(m.group()) if _luhn_ok(m.group()) else m.group(),
        text,
    )
    return text

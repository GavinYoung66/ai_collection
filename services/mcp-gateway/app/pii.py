"""网关出口 PII 递归脱敏（Batch 4，任务 5.3/5.4，design D4 第一道）。

在 call_tool 返回离开网关前递归遍历结果（str/int/dict/list/tuple），覆盖四类敏感数据：
- 手机号：``mask_text`` 正则 ``1[3-9]\\d{9}`` → 138****1234；
- 身份证：18 位连续数字 → 110101********1234；
- 银行卡：16~19 位连续数字 → **** **** **** 1234；
- 姓名：人名字段（有界 key 白名单）整体 ``mask_name`` → 张**。

数字型 PII（手机号/身份证/银行卡以 JSON number 而非 string 传输）同样覆盖：
转 str 后走同一组 mask 正则，命中则以脱敏字符串替换（类型让位于安全，
无明文残留优先于保形）；未命中的普通数字原样返回保持类型。bool 虽是 int
子类但 SHALL NOT 参与数字判定。tuple 与 list 同构递归。float 不处理
（手机号/证件号不以浮点传输，且指数/小数点不匹配数字正则）。

人名字段使用有界白名单而非所有 ``*_name`` 键：避免误伤 rule_name /
template_name / kb_name 等非人名字段（RAG 结果中常见）。白名单覆盖当前
工具契约中的全部人名字段（business_ticket.customer_name 等）及常见变体。

与 complaint-service ``MCPClient._sanitize_result`` 的差异：Agent 侧二次校验
仅做 mask_text 正则脱敏（str/dict/list）；网关作为第一道额外承担姓名字段
mask_name 与数字型/tuple 覆盖（本批任务明确要求 + 审计整改 P1-2）。检测语义
与 Agent 侧 ``has_plaintext_pii`` 对齐（mask 前后是否变化）。
"""
from __future__ import annotations

from typing import Any

from common.security.mask import mask_name, mask_text

# 人名字段 key 白名单（比较时忽略大小写）
NAME_KEYS = frozenset(
    {
        "name",
        "real_name",
        "person_name",
        "customer_name",
        "user_name",
        "contact_name",
        "agent_name",
        "holder_name",
    }
)


def _is_name_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in NAME_KEYS


def _string_has_pii(text: str, *, name_field: bool) -> bool:
    """检测单个字符串是否含明文 PII（mask 前后变化判定，对齐 has_plaintext_pii）。"""
    if mask_text(text) != text:
        return True
    if name_field and mask_name(text) != text:
        return True
    return False


def _int_masked(value: int) -> str | None:
    """数字型 PII 判定：转 str 走同一组 mask 正则；命中返回脱敏串，否则 None。"""
    text = str(value)
    masked = mask_text(text)
    return masked if masked != text else None


def contains_pii(value: Any, *, name_field: bool = False) -> bool:
    """递归检测返回结构中是否含明文 PII（用于触发 5.4 审计事件）。

    name_field 仅对 dict 中人名字段对应的值开启，并向其 list/tuple 元素传播
    （如 ``names: ["张三", "李四"]``）；普通元素不做姓名判定。
    """
    if isinstance(value, bool):
        # bool 是 int 子类：True/False SHALL NOT 被当作数字型 PII
        return False
    if isinstance(value, str):
        return _string_has_pii(value, name_field=name_field)
    if isinstance(value, int):
        return _int_masked(value) is not None
    if isinstance(value, dict):
        return any(
            contains_pii(v, name_field=_is_name_key(k)) for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_pii(item, name_field=name_field) for item in value)
    return False


def _sanitize_text(text: str, *, name_field: bool) -> str:
    # 先做手机号/身份证/银行卡正则脱敏（纯数字替换，不影响中文姓名），
    # 再做姓名字段整体脱敏；二者处理字符集正交，顺序无叠加风险。
    sanitized = mask_text(text)
    if name_field:
        sanitized = mask_name(sanitized)
    return sanitized


def sanitize(value: Any, *, name_field: bool = False) -> Any:
    """递归脱敏：返回同构的新结构，SHALL NOT 原地修改入参。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _sanitize_text(value, name_field=name_field)
    if isinstance(value, int):
        # 命中数字型 PII → 脱敏字符串；普通数字原样返回保持类型
        return _int_masked(value) or value
    if isinstance(value, dict):
        return {k: sanitize(v, name_field=_is_name_key(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        cleaned = [sanitize(item, name_field=name_field) for item in value]
        return tuple(cleaned) if isinstance(value, tuple) else cleaned
    return value

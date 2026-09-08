"""明文 PII 访问 RBAC + 审计（10.3）。

权限矩阵（对齐 design D6 / step8 10.3）：
| 角色              | 明文 PII | 说明                          |
|-------------------|----------|-------------------------------|
| operator_final    | ✓        | 运营终审，可查看明文          |
| operator_reviewer | ✗        | 运营一审/二审，仅脱敏         |
| developer         | ✗        | 研发，禁生产明文              |
| ops               | ✗        | 运维，仅脱敏日志              |
| admin             | 双人授权 | 安全审计解密 CoT 需双人授权   |

每次明文访问写入 audit_logs（common.security.audit.log_action）。
"""
from __future__ import annotations

from common.security.audit import log_action

# 可直接访问明文的角色
_PLAINTEXT_ROLES = {"operator_final"}

# 需双人授权的角色
_DUAL_APPROVAL_ROLES = {"admin"}


def can_access_plaintext(role: str, dual_approved: bool = False) -> bool:
    """判断角色是否可访问明文 PII。

    Args:
        role: 角色名
        dual_approved: admin 等角色是否已完成双人授权
    """
    if role in _PLAINTEXT_ROLES:
        return True
    if role in _DUAL_APPROVAL_ROLES:
        return dual_approved
    return False


def access_plaintext(role: str, resource: str = "", *, trace_id: str = "",
                     dual_approved: bool = False) -> bool:
    """访问明文 PII：先鉴权，通过则写审计，返回是否放行。"""
    if not can_access_plaintext(role, dual_approved=dual_approved):
        log_action(
            action="plaintext_access_denied",
            user_id=role,
            resource=resource,
            trace_id=trace_id,
        )
        return False
    log_action(
        action="plaintext_access_granted",
        user_id=role,
        resource=resource,
        trace_id=trace_id,
    )
    return True

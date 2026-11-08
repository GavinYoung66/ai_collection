"""URL path 参数白名单（T79/T111 单源，spec runtime-correctness：单源收敛完整性）。

时间解析、URL path 参数白名单与 DSN 构造 MUST 复用单一事实源，MUST NOT 在
调用点保留等价本地实现。本模块是全仓 URL path 参数白名单的唯一事实源：
qc→dp（dp_client，T79）与 dp→qc/complaint（qc_client/complaint_client，T111）
共用同一正则与校验函数。
"""
from __future__ import annotations

import re

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

# T79/T111：URL path 参数白名单（spec §6.6）——session_id/complaint_id 等仅
# 允许字母/数字/*/-/_（`_` 用于 fixture 与常见 id 形态，如 sess_fixture_001；
# 明确排除 `.`，防 `..` 段归一化改写路径）
PATH_PARAM_RE = re.compile(r"^[A-Za-z0-9*_-]{1,64}$")


def is_safe_path_param(value: str | None) -> bool:
    """URL path 参数是否合法（纯判定，不抛错）。"""
    return bool(PATH_PARAM_RE.match(value or ""))


def ensure_safe_path_param(value: str, *, name: str = "session_id") -> str:
    """校验 URL path 参数并原样返回；非法值抛 BizException（A2002）。

    非法示例：``x/../y``（路径穿越）、含 ``?``/``#``（改写请求语义）、
    含 ``.``（``..`` 段归一化）、超长（>64，与各服务 VARCHAR(64) 对齐）。
    """
    if not PATH_PARAM_RE.match(value or ""):
        raise BizException(ErrorCode.A2002, f"非法 {name}: {value!r}")
    return value
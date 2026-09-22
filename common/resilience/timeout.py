"""三级超时配置：connect / read / total。

层级约定（下游超时必须先于上游调用方触发）：

    MCP  total=30s   <   LLM total=120s   <   投诉全局 30min(1800s)

- connect：网络不可达时快速失败，避免吃 OS 默认长超时；
- read：下游响应慢时失败；
- total：整次调用的硬上限，即使 read 未到点也会被 total 截断。

注意 total 是硬上限：允许 read 大于 total（如 MCP ``Timeout(total=30.0)``
沿用默认 read=60.0），实际单次读等待由 min(read, total 剩余预算) 生效。
"""
from __future__ import annotations

from dataclasses import dataclass

# 超时层级参考值（秒），供各服务直接引用，避免各处魔法数字。
MCP_TOTAL_TIMEOUT = 30.0
LLM_TOTAL_TIMEOUT = 120.0
COMPLAINT_GLOBAL_TIMEOUT = 1800.0  # 30min


@dataclass(frozen=True)
class Timeout:
    """三级超时（秒）。frozen 保证配置对象在传递过程中不被篡改。"""

    connect: float = 5.0
    read: float = 60.0
    total: float = 120.0

    def __post_init__(self) -> None:
        # total 是硬上限，read/connect 允许配置得大于 total（total 先生效截断），
        # 因此这里只校验正数，不强制 read/connect <= total。
        for field_name in ("connect", "read", "total"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"timeout.{field_name} 必须为正数，实际: {value!r}")

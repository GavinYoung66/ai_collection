"""CSMP 智能服务集群平台公共件。

顶层导出全仓公共原语，供各服务 ``from common import spawn_tracked`` 使用。
"""
from common.utils.spawn import spawn_tracked

__all__ = ["spawn_tracked"]
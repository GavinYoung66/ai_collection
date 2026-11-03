"""兼容入口：重试实现已收敛到 ``common.resilience.retry``（T28）。"""
from common.resilience.retry import retry

__all__ = ["retry"]

"""自定义异常类。"""
from common.errors.codes import ErrorCode, ERROR_MESSAGES


class BizException(Exception):
    """业务异常，返回 4xx + A 系列错误码。"""

    def __init__(self, code: ErrorCode, message: str | None = None):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code.value, code.value)
        super().__init__(self.message)


class SysException(Exception):
    """系统异常，返回 5xx + B 系列错误码。"""

    def __init__(self, code: ErrorCode = ErrorCode.B0007, message: str | None = None):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code.value, code.value)
        super().__init__(self.message)

"""呼叫中心能力包：阿里云 CCC 外呼/转人工/挂断封装。"""
from common.callcenter.provider import CCCProvider, CallResult, TransferResult, ccc_provider

__all__ = ["CCCProvider", "CallResult", "TransferResult", "ccc_provider"]

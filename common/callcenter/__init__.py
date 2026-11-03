"""呼叫中心能力包：阿里云 CCC 外呼/转人工/挂断封装。

本包不持有业务配置；调用方（call-service）用自身的 ``CallSettings`` 注入并发上限。
"""
from common.callcenter.provider import CCCProvider, CallResult, TransferResult

__all__ = ["CCCProvider", "CallResult", "TransferResult"]

"""阿里云 CCC（云呼叫中心）HTTP API 封装。"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import base64
import time
from dataclasses import dataclass

import aiohttp
import structlog

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.llm.reliability import is_placeholder_secret
from common.utils.retry import retry
from common.utils.semaphore import CallSemaphore

logger = structlog.get_logger()


@dataclass
class CallResult:
    call_id: str
    status: str


@dataclass
class TransferResult:
    session_id: str
    status: str


class CCCProvider:
    """封装阿里云呼叫中心核心操作：外呼、转人工、挂断。"""

    def __init__(self) -> None:
        self._access_key_id = settings.CCC_ACCESS_KEY_ID
        self._access_key_secret = settings.CCC_ACCESS_KEY_SECRET
        self._max_concurrency = settings.CALL_MAX_CONCURRENCY
        self._semaphore = CallSemaphore(self._max_concurrency)

    def _sign(self, params: dict, timestamp: str) -> str:
        sorted_params = sorted(params.items())
        canonical = "&".join(f"{k}={v}" for k, v in sorted_params)
        string_to_sign = f"POST&%2F&{base64.b64encode(canonical.encode()).decode()}"
        signing_key = self._access_key_secret + "&"
        signature = hmac.new(
            signing_key.encode(), string_to_sign.encode(), hashlib.sha1
        ).digest()
        return base64.b64encode(signature).decode()

    def _common_params(self, action: str) -> dict:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        params = {
            "Format": "JSON",
            "Version": "2017-07-12",
            "AccessKeyId": self._access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": timestamp,
            "SignatureVersion": "1.0",
            "SignatureNonce": str(int(time.time() * 1000)),
            "Action": action,
        }
        return params

    @retry(max_attempts=3, delay=1.0, backoff=2.0, exceptions=(aiohttp.ClientError, asyncio.TimeoutError))
    async def _post(self, action: str, extra: dict) -> dict:
        if settings.ENV == "dev" and is_placeholder_secret(self._access_key_id):
            logger.info("ccc_mock_mode", action=action)
            return {"mock": True, "action": action, **extra}

        params = self._common_params(action)
        params.update(extra)
        timestamp = params["Timestamp"]
        params["Signature"] = self._sign(params, timestamp)
        url = "https://ccc.cn-shanghai.aliyuncs.com/"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, data=params, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status != 200:
                        raise BizException(ErrorCode.A1013, f"CCC 调用失败: HTTP {resp.status}")
                    return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("ccc_failed", action=action, error=str(exc))
            raise BizException(ErrorCode.A1013, "呼叫中心链路异常") from exc

    async def make_call(self, phone: str, case_id: str, session_id: str) -> CallResult:
        """发起外呼。

        Args:
            phone: 被叫号码。
            case_id: 案件 ID。
            session_id: 会话 ID。

        Returns:
            CallResult: call_id + status。
        """
        async with self._semaphore:
            result = await self._post(
                "PickOutboundNumbers",
                {
                    "InstanceId": "mock_instance",
                    "CalledNumber": phone,
                    "CallId": session_id,
                },
            )
            call_id = result.get("CallId", session_id)
            status = result.get("status", "ringing")
            logger.info("ccc_make_call", phone=phone, case_id=case_id, call_id=call_id)
            return CallResult(call_id=call_id, status=status)

    async def transfer_to_human(self, session_id: str, agent_id: str) -> TransferResult:
        """转人工坐席。

        Args:
            session_id: 当前会话 ID。
            agent_id: 目标坐席 ID。

        Returns:
            TransferResult: session_id + status。
        """
        result = await self._post(
            "TransferToAgent",
            {"CallId": session_id, "AgentId": agent_id},
        )
        status = result.get("status", "transferred")
        logger.info("ccc_transfer", session_id=session_id, agent_id=agent_id)
        return TransferResult(session_id=session_id, status=status)

    async def hangup(self, session_id: str) -> bool:
        """挂断通话。

        Args:
            session_id: 会话 ID。

        Returns:
            bool: 是否挂断成功。
        """
        result = await self._post("HangupCall", {"CallId": session_id})
        success = result.get("mock") or result.get("status") == "ok"
        logger.info("ccc_hangup", session_id=session_id, success=success)
        return bool(success)


ccc_provider = CCCProvider()

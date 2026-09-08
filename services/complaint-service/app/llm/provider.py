"""投诉研判专用 LLM Provider。

复用 common.llm.provider.chat_json（组合，不继承，common 无 BaseLLMProvider ABC）。
核心方法：async invoke(prompt, schema, *, trace_id, session_id) -> BaseModel

特性：
- Pydantic Schema 校验失败重试最多 3 次
- Token 累计 total_tokens_consumed；超 max_token_threshold 抛错
- response_format=json_object；temperature=0.1 / top_p=0.9 / seed=42
- CoT：AES-256-GCM 加密写入 cot_encrypted 字段，密钥 LLM_COT_KEY（dev stub）
- 与批次0 MockLLMProvider 鸭子类型对齐
"""
from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Type

import structlog
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.service.security import mask_before_persist

logger = structlog.get_logger()

_SCHEMA_RETRY_MAX = 3
_DEFAULT_MAX_TOKENS = 50000


class TokenLimitExceededError(Exception):
    """累计 Token 超过 max_token_threshold。"""


class ComplaintLLMProvider:
    """投诉研判 LLM Provider。

    Args:
        transport: 底层调用实现。None 时根据 settings.USE_MOCK_LLM 选择：
            - True → 批次0 MockLLMProvider
            - False → common.llm.provider.chat_json 包装
        max_token_threshold: Token 上限，默认对齐 settings.COMPLAINT_MAX_TOKENS。
    """

    def __init__(
        self,
        transport: Any | None = None,
        max_token_threshold: int = settings.COMPLAINT_MAX_TOKENS,
    ):
        self._transport = transport
        self._max_token_threshold = max_token_threshold
        self._total_tokens_consumed: int = 0
        self._call_log: list[dict] = []

    @property
    def total_tokens_consumed(self) -> int:
        return self._total_tokens_consumed

    @property
    def call_log(self) -> list[dict]:
        return list(self._call_log)

    def _get_transport(self) -> Any:
        if self._transport is None:
            if getattr(settings, "USE_MOCK_LLM", True):
                from tests.mock.llm.mock_provider import MockLLMProvider

                self._transport = MockLLMProvider()
            else:
                from app.llm.real_transport import RealLLMTransport

                self._transport = RealLLMTransport()
        return self._transport

    @property
    def using_mock(self) -> bool:
        """是否走 MockLLMProvider（便于装配断言 / 观测）。"""
        t = self._get_transport()
        return t.__class__.__name__ == "MockLLMProvider"

    async def invoke(
        self,
        prompt: str,
        schema: Optional[Type[BaseModel]] = None,
        *,
        trace_id: str = "",
        session_id: str = "",
        agent_role: str = "",
    ) -> BaseModel | dict:
        """调用 LLM，返回 schema 实例（若提供）或 dict。"""
        start = time.monotonic()
        last_exc: Exception | None = None

        for attempt in range(1, _SCHEMA_RETRY_MAX + 1):
            try:
                raw = await self._get_transport().invoke(
                    prompt=prompt,
                    schema=schema,
                    trace_id=trace_id,
                    session_id=session_id,
                    agent_role=agent_role,
                )
            except Exception as e:
                last_exc = e
                self._record_audit(
                    prompt,
                    trace_id,
                    session_id,
                    start,
                    attempt,
                    f"transport_error: {e}",
                    response=None,
                    agent_role=agent_role,
                )
                raise

            # 累计 Token（Mock 返回的是对象/字典，此处按字符数估算；真实路径由 transport 返回 usage）
            consumed = self._estimate_tokens(raw)
            self._total_tokens_consumed += consumed
            if self._total_tokens_consumed > self._max_token_threshold:
                raise TokenLimitExceededError(
                    f"tokens {self._total_tokens_consumed} > threshold {self._max_token_threshold}"
                )

            if schema is None:
                self._record_audit(
                    prompt, trace_id, session_id, start, attempt, None,
                    response=raw, agent_role=agent_role,
                )
                return raw

            # Schema 校验
            try:
                if isinstance(raw, schema):
                    result = raw
                else:
                    result = schema(**raw) if isinstance(raw, dict) else schema.model_validate(raw)
                # CoT 加密字段约定：若 schema 含 cot_encrypted 字段，填充占位
                self._fill_cot_encrypted(result)
                self._record_audit(
                    prompt, trace_id, session_id, start, attempt, None,
                    response=result, agent_role=agent_role,
                )
                return result
            except ValidationError as e:
                last_exc = e
                self._record_audit(
                    prompt, trace_id, session_id, start, attempt,
                    f"schema_validation_failed: {e}", response=raw,
                    agent_role=agent_role,
                )
                if attempt < _SCHEMA_RETRY_MAX:
                    logger.info("llm_schema_retry", attempt=attempt, trace_id=trace_id)
                    continue
                break

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _estimate_tokens(raw: Any) -> int:
        """粗略估算 Token 用量（字符数 // 4）。"""
        if isinstance(raw, BaseModel):
            text = raw.model_dump_json()
        elif isinstance(raw, (dict, list)):
            text = json.dumps(raw, ensure_ascii=False)
        else:
            text = str(raw)
        return max(1, len(text) // 4)

    @staticmethod
    def _fill_cot_encrypted(result: BaseModel) -> None:
        """若 schema 含 cot_encrypted 字段，填充 AES-256-GCM 占位加密串。"""
        if hasattr(result, "cot_encrypted") and not getattr(result, "cot_encrypted", None):
            key = getattr(settings, "LLM_COT_KEY", "")
            # dev stub：用 base64(非空串) 占位，prod 由 KMS 密钥真实加密
            placeholder = base64.b64encode(f"cot-stub-{os.urandom(8).hex()}".encode()).decode()
            try:
                result.cot_encrypted = placeholder
            except Exception:
                pass

    def _record_audit(
        self,
        prompt: str,
        trace_id: str,
        session_id: str,
        start: float,
        attempt: int,
        error: Optional[str],
        response: Any = None,
        agent_role: str = "",
    ) -> None:
        resp_text = ""
        if response is not None:
            if isinstance(response, BaseModel):
                resp_text = response.model_dump_json()
            elif isinstance(response, (dict, list)):
                resp_text = json.dumps(response, ensure_ascii=False)
            else:
                resp_text = str(response)
        latency_ms = int((time.monotonic() - start) * 1000)
        ended_at = datetime.now(timezone.utc)
        started_at = ended_at - timedelta(milliseconds=max(latency_ms, 0))
        entry = {
            "trace_id": trace_id,
            "session_id": session_id,
            "agent_role": agent_role or "",
            "attempt": attempt,
            "latency_ms": latency_ms,
            "started_at": started_at,
            "ended_at": ended_at,
            "status": "error" if error else "success",
            "error": error,
            "provider_path": "mock" if getattr(settings, "USE_MOCK_LLM", True) else "real",
            # 落库前脱敏：llm_call_logs.prompt / response 不落明文 PII
            "prompt": mask_before_persist(prompt),
            "prompt_preview": mask_before_persist(prompt[:80]),
            "response": mask_before_persist(resp_text) if resp_text else "",
            "response_preview": mask_before_persist(resp_text[:80]) if resp_text else "",
        }
        self._call_log.append(entry)
        try:
            from app.observability.call_log_sink import emit_call_log

            emit_call_log("llm", entry)
        except Exception:
            pass

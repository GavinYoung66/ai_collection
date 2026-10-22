"""投诉研判专用 LLM Provider。

复用 common.llm.provider.chat_json（组合，不继承，common 无 BaseLLMProvider ABC）。
核心方法：async invoke(prompt, schema, *, trace_id, session_id) -> BaseModel

特性：
- 统一入口走 common.llm.reliability.invoke_llm：熔断检查 → 有界重试 →
  fallback_chain 换模型（主→备→更弱）→ 降级 flag（spec llm-layer）
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
from common.llm.reliability import LLMCircuitOpenError, invoke_llm, set_default_service
from common.resilience.breaker import make_llm_breaker_key
from common.resilience.timeout import Timeout

logger = structlog.get_logger()

# spec 术语对齐：熔断打开错误统一名（common 实现为 LLMCircuitOpenError）
CircuitBreakerOpenError = LLMCircuitOpenError

# 本服务韧性 key 前缀（投诉服务固定 csmp）
_SERVICE_NAME = "csmp"

_SCHEMA_RETRY_MAX = 3
_DEFAULT_MAX_TOKENS = 50000


class TokenLimitExceededError(Exception):
    """累计 Token 超过 max_token_threshold。"""


class LLMCallError(Exception):
    """fallback_chain 整链失败（invoke_llm 返回 degraded outcome）。"""


def get_llm_fallback_chain() -> list[str]:
    """解析 LLM fallback_chain（有序：主模型 → 备用 → 更弱模型）。

    - settings.LLM_FALLBACK_CHAIN 非空：逗号拆分，顺序即优先级（主模型在前）；
    - 为空：回退 LLM_MODEL_PRIMARY / FALLBACK_1 / FALLBACK_2 推导（向后兼容）；
    - 校验：全链非空、去重后无空项；重复项 fail-fast（配置错误宁可启动报错）。
    """
    raw = (getattr(settings, "LLM_FALLBACK_CHAIN", "") or "").strip()
    if raw:
        chain = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        chain = [
            getattr(settings, "LLM_MODEL_PRIMARY", "") or "",
            getattr(settings, "LLM_MODEL_FALLBACK_1", "") or "",
            getattr(settings, "LLM_MODEL_FALLBACK_2", "") or "",
        ]
        chain = [m for m in chain if m]
    if not chain:
        raise RuntimeError(
            "LLM fallback_chain 为空：请配置 LLM_FALLBACK_CHAIN "
            "或 LLM_MODEL_PRIMARY / LLM_MODEL_FALLBACK_1 / LLM_MODEL_FALLBACK_2"
        )
    if len(set(chain)) != len(chain):
        raise RuntimeError(f"LLM fallback_chain 含重复模型，顺序非法: {chain}")
    return chain


def _serialize_raw(raw: Any) -> str:
    """把 transport 返回值序列化为字符串（invoke_llm content 契约）。"""
    if isinstance(raw, BaseModel):
        return raw.model_dump_json()
    if isinstance(raw, (dict, list)):
        return json.dumps(raw, ensure_ascii=False)
    return str(raw)


def _format_schema_repair_hint(err: ValidationError) -> str:
    """把 Pydantic 校验错误压缩成模型可读的定向修正指令。

    只取 loc + msg + type，最多 10 条，避免错误信息过长挤占上下文。
    """
    lines: list[str] = []
    for item in err.errors()[:10]:
        loc = ".".join(str(p) for p in item.get("loc", ())) or "<root>"
        lines.append(f"- 字段 {loc}: {item.get('msg', '')} (type={item.get('type', '')})")
    detail = "\n".join(lines) if lines else "- 输出结构不符合约定"
    return (
        "上一次输出不符合约定的结构，请严格按 Schema 修正后重新输出，"
        "不要添加额外字段或改变字段类型。\n"
        f"{detail}"
    )


class ComplaintLLMProvider:
    """投诉研判 LLM Provider。

    Args:
        transport: 底层调用实现。None 时根据 settings.USE_MOCK_LLM 选择：
            - True → 批次0 MockLLMProvider
            - False → common.llm.provider.chat_json 包装
        max_token_threshold: Token 上限，默认对齐 settings.COMPLAINT_MAX_TOKENS。
        llm_timeout: 统一编排的三级超时，默认 Timeout(total=120.0)（LLM 层级）。
    """

    def __init__(
        self,
        transport: Any | None = None,
        max_token_threshold: int = settings.COMPLAINT_MAX_TOKENS,
        llm_timeout: Timeout | None = None,
    ):
        self._transport = transport
        self._max_token_threshold = max_token_threshold
        self._llm_timeout = llm_timeout or Timeout(total=120.0)
        self._total_tokens_consumed: int = 0
        self._call_log: list[dict] = []
        # 韧性 key 派生依赖服务名（invoke_llm 熔断/降级 key 前缀），幂等设置
        set_default_service(_SERVICE_NAME)

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
        """调用 LLM，返回 schema 实例（若提供）或 dict。

        统一走 common.llm.reliability.invoke_llm：主模型失败（超时/熔断 open/
        业务错重试耗尽）自动换 fallback_chain 下一模型；整链失败抛 LLMCallError，
        由节点级降级路径接管（规则草稿 + pending_review），不冒泡 500。
        """
        start = time.monotonic()
        chain = get_llm_fallback_chain()
        primary, fallbacks = chain[0], chain[1:]
        messages = [{"role": "user", "content": prompt}]

        async def _transport_adapter(
            model: str, _messages: list[dict], timeout_seconds: float
        ) -> str:
            # 适配 invoke_llm 传输签名 (model, messages, timeout_seconds) -> str：
            # prompt 由 messages 实时拼装——校验失败重试时会追加结构修正指令，
            # 使重试从「同 prompt 重采样」变为「带错误反馈的定向修复」；
            # 透传目标模型以支持 fallback 换模型。
            prompt_text = "\n\n".join(
                str(m.get("content", "")) for m in _messages if m.get("content")
            )
            raw = await self._get_transport().invoke(
                prompt=prompt_text,
                schema=schema,
                trace_id=trace_id,
                session_id=session_id,
                agent_role=agent_role,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            return _serialize_raw(raw)

        last_exc: Exception | None = None

        for attempt in range(1, _SCHEMA_RETRY_MAX + 1):
            try:
                result = await invoke_llm(
                    model=primary,
                    messages=messages,
                    fallback_chain=fallbacks,
                    timeout=self._llm_timeout,
                    breaker_key=make_llm_breaker_key(_SERVICE_NAME, primary),
                    transport=_transport_adapter,
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

            # 整条 fallback_chain 耗尽 → degraded outcome，转 LLMCallError 交节点降级
            if result.get("degraded") and result.get("content") is None:
                last_exc = LLMCallError(result.get("error") or "all models failed")
                self._record_audit(
                    prompt,
                    trace_id,
                    session_id,
                    start,
                    attempt,
                    f"llm_chain_degraded: {last_exc}",
                    response=None,
                    agent_role=agent_role,
                )
                raise last_exc

            # 解析内容（adapter 已序列化为字符串）
            content = result["content"]
            try:
                raw = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                raw = {"raw_text": content}

            # 累计 Token（按解析结果字符数估算；真实路径由 transport 返回 usage）
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
                result_model = (
                    raw
                    if isinstance(raw, schema)
                    else schema(**raw) if isinstance(raw, dict) else schema.model_validate(raw)
                )
                # CoT 加密字段约定：若 schema 含 cot_encrypted 字段，填充占位
                self._fill_cot_encrypted(result_model)
                self._record_audit(
                    prompt, trace_id, session_id, start, attempt, None,
                    response=result_model, agent_role=agent_role,
                )
                return result_model
            except ValidationError as e:
                last_exc = e
                self._record_audit(
                    prompt, trace_id, session_id, start, attempt,
                    f"schema_validation_failed: {e}", response=raw,
                    agent_role=agent_role,
                )
                if attempt < _SCHEMA_RETRY_MAX:
                    # 把校验错误回灌给模型：追加修正指令，使其定向修复而非盲目重采样
                    messages = [
                        *messages,
                        {"role": "user", "content": _format_schema_repair_hint(e)},
                    ]
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

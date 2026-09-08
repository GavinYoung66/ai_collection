"""包装真实 LLM 调用：按 model_router 选型，DeepSeek / DashScope 分 endpoint。

经 common.llm.transport 发 OpenAI 兼容请求；可靠性经 common.llm.reliability。
业务 system prompt 自 prompt/templates YAML 加载（DEBT-01）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Type

import structlog
import yaml
from pydantic import BaseModel

from app.config import settings
from app.llm.router import ModelRouter
from app.utils.redis_keys import LLM_DEGRADED_KEY, TTL_LLM_DEGRADED
from common.llm.reliability import RedisDegradeTracker, call_with_retry, is_placeholder_secret
from common.llm.transport import message_content_from_completions, post_chat_completions

logger = structlog.get_logger()

# 兼容既有单测：`from app.llm.real_transport import _is_placeholder`
_is_placeholder = is_placeholder_secret

_SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompt" / "templates" / "complaint_real_llm_system.yaml"
)
_FAILURE_THRESHOLD = 3
# RedisDegradeTracker 需要正 TTL；永不过期键用较长窗口近似（与 breaker 永久 flag 并存）
_DEGRADE_TTL = int(TTL_LLM_DEGRADED) if TTL_LLM_DEGRADED else 3600


@lru_cache(maxsize=1)
def _load_system_prompt_base() -> str:
    with open(_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    text = (data.get("system") or "").strip()
    if not text:
        raise RuntimeError(f"missing system prompt in {_SYSTEM_PROMPT_PATH}")
    return text


class RealLLMTransport:
    """真实模型 transport，对齐 MockLLMProvider.invoke 签名。"""

    def __init__(
        self,
        router: ModelRouter | None = None,
        *,
        redis: Any | None = None,
        degrade: RedisDegradeTracker | None = None,
    ):
        self._router = router or ModelRouter()
        self.last_model: str | None = None
        self.last_provider: str | None = None
        self._degrade = degrade or RedisDegradeTracker(
            redis,
            key=LLM_DEGRADED_KEY,
            failure_threshold=_FAILURE_THRESHOLD,
            ttl_seconds=_DEGRADE_TTL,
        )

    def _resolve_role(self, agent_role: str, prompt: str, schema: Optional[Type[BaseModel]]) -> str:
        if agent_role:
            return agent_role
        name = (schema.__name__ if schema is not None else "").lower()
        mapping = {
            "verdictdraft": "verdict",
            "complianceopinion": "compliance",
            "facttimeline": "fact",
            "userdemandanalysis": "user_demand",
            "reflectionreport": "reflection",
            "judgmentpackage": "generate_package",
        }
        if name in mapping:
            return mapping[name]
        p = prompt or ""
        for key, role in (
            ("判责", "verdict"),
            ("合规", "compliance"),
            ("时间线", "fact"),
            ("诉求", "user_demand"),
            ("反思", "reflection"),
            ("答辩", "generate_package"),
        ):
            if key in p:
                return role
        return "verdict"

    def _provider_for_model(self, model: str) -> tuple[str, str, str]:
        """返回 (provider_name, api_key, base_url)。"""
        import os

        m = (model or "").lower()
        if m.startswith("deepseek"):
            key = (
                getattr(settings, "LLM_DEEPSEEK_API_KEY", "")
                or getattr(settings, "DEEPSEEK_API_KEY", "")
                or os.environ.get("LLM_DEEPSEEK_API_KEY", "")
                or os.environ.get("DEEPSEEK_API_KEY", "")
                or os.environ.get("DeepSeek_API_KEY", "")
                or ""
            )
            base = getattr(settings, "LLM_DEEPSEEK_BASE_URL", "") or "https://api.deepseek.com"
            return "deepseek", key, base.rstrip("/")
        key = (
            getattr(settings, "LLM_DASHSCOPE_API_KEY", "")
            or getattr(settings, "LLM_API_KEY", "")
            or getattr(settings, "DASHSCOPE_API_KEY", "")
            or os.environ.get("LLM_DASHSCOPE_API_KEY", "")
            or os.environ.get("LLM_API_KEY", "")
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or ""
        )
        base = (
            getattr(settings, "LLM_DASHSCOPE_BASE_URL", "")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        return "dashscope", key, base.rstrip("/")

    @staticmethod
    def _api_model_name(model: str, provider: str) -> str:
        """路由配置名 → 供应商实际可调用模型名。"""
        m = (model or "").strip()
        if provider == "deepseek":
            # model_router 写 deepseek-r1；官方兼容接口常用 deepseek-reasoner / deepseek-chat
            if m in ("deepseek-r1", "deepseek-reasoner"):
                return "deepseek-reasoner"
            if m.startswith("deepseek"):
                return m
            return "deepseek-chat"
        return m

    def _resolve_model_and_creds(self, role: str) -> tuple[str, str, str, str]:
        try:
            model = self._router.route(role)
        except ValueError:
            model = getattr(settings, "LLM_MODEL_PRIMARY", None) or getattr(
                settings, "LLM_MODEL", "qwen-plus"
            )
        if not model:
            model = getattr(settings, "LLM_MODEL", "qwen-plus")

        provider, api_key, base_url = self._provider_for_model(model)
        if is_placeholder_secret(api_key) and provider == "deepseek":
            dash_key = (
                getattr(settings, "LLM_DASHSCOPE_API_KEY", "")
                or getattr(settings, "LLM_API_KEY", "")
                or getattr(settings, "DASHSCOPE_API_KEY", "")
                or ""
            )
            if not is_placeholder_secret(dash_key):
                logger.warning(
                    "deepseek_key_missing_fallback_qwen",
                    requested_model=model,
                    agent_role=role,
                )
                model = "qwen-plus"
                provider, api_key, base_url = self._provider_for_model(model)
        if is_placeholder_secret(api_key):
            raise RuntimeError(
                f"real LLM key missing for provider={provider} model={model}; "
                f"set LLM_DEEPSEEK_API_KEY / LLM_DASHSCOPE_API_KEY (or LLM_API_KEY) via env, "
                f"keep USE_MOCK_LLM=true"
            )
        return model, provider, api_key, base_url

    def _build_system(self, role: str, schema: Optional[Type[BaseModel]]) -> str:
        system = _load_system_prompt_base()
        if role:
            system += f" 当前 agent_role={role}。"
        if schema is not None:
            try:
                schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)[:4000]
                system += f" JSON schema hint: {schema_hint}"
            except Exception:
                pass
        return system

    async def invoke(
        self,
        prompt: str,
        schema: Optional[Type[BaseModel]] = None,
        *,
        trace_id: str = "",
        session_id: str = "",
        agent_role: str = "",
        **_: Any,
    ) -> dict:
        role = self._resolve_role(agent_role, prompt, schema)
        model, provider, api_key, base_url = self._resolve_model_and_creds(role)
        self.last_model = model
        self.last_provider = provider
        system = self._build_system(role, schema)

        try:
            return await self._post_chat(
                prompt=prompt,
                system=system,
                model=model,
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                agent_role=role,
                trace_id=trace_id,
                session_id=session_id,
            )
        except RuntimeError as e:
            # DashScope 免费额度耗尽等：自动切 DeepSeek，保证本地真图可跑通
            msg = str(e)
            if provider != "dashscope" or ("HTTP 403" not in msg and "HTTP 429" not in msg):
                raise
            ds_model = "deepseek-chat"
            ds_provider, ds_key, ds_base = self._provider_for_model(ds_model)
            if is_placeholder_secret(ds_key):
                raise
            logger.warning(
                "dashscope_fallback_deepseek",
                error=msg[:200],
                agent_role=role,
                fallback_model=ds_model,
            )
            return await self._post_chat(
                prompt=prompt,
                system=system,
                model=ds_model,
                provider=ds_provider,
                api_key=ds_key,
                base_url=ds_base,
                agent_role=role,
                trace_id=trace_id,
                session_id=session_id,
            )

    async def _post_chat(
        self,
        *,
        prompt: str,
        system: str,
        model: str,
        provider: str,
        api_key: str,
        base_url: str,
        agent_role: str,
        trace_id: str,
        session_id: str,
    ) -> dict:
        self.last_model = model
        self.last_provider = provider
        api_model = self._api_model_name(model, provider)
        logger.info(
            "real_llm_invoke",
            provider=provider,
            model=model,
            api_model=api_model,
            agent_role=agent_role,
            trace_id=trace_id,
            session_id=session_id,
            provider_path="real",
        )

        if await self._degrade.is_degraded():
            raise RuntimeError(
                f"LLM service is in circuit-break state key={self._degrade.key}"
            )

        async def _once() -> dict:
            try:
                body = await post_chat_completions(
                    base_url=base_url,
                    api_key=api_key,
                    model=api_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=120.0,
                    temperature=0.1,
                    top_p=0.9,
                    seed=42,
                    response_format={"type": "json_object"},
                )
            except RuntimeError as e:
                raise RuntimeError(
                    f"real LLM {e} provider={provider} model={model} api_model={api_model}"
                ) from e

            raw = message_content_from_completions(body)
            if not raw:
                raw = json.dumps(body, ensure_ascii=False) if body else "{}"
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}")
                if start >= 0 and end > start:
                    try:
                        data = json.loads(raw[start : end + 1])
                    except json.JSONDecodeError:
                        data = {"raw_text": raw}
                else:
                    data = {"raw_text": raw}
            if not isinstance(data, dict):
                data = {"value": data}
            return data

        try:
            data = await call_with_retry(_once, max_attempts=3, delay=0.5, backoff=2.0)
            await self._degrade.on_success()
            return data
        except Exception as e:
            await self._degrade.on_failure(e)
            raise

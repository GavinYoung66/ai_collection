"""LLM 语义复核：common.llm.transport + qc_review_v1；熔断跳过仍出 AC+规则分。"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Awaitable, Callable, Sequence
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import qc_settings
from app.events.publisher import QcEventPublisher
from app.observability import emit_llm_review_completed, emit_llm_review_degraded, emit_llm_review_started
from app.repositories.job_store import QcJobStore, llm_chain_models, llm_degraded_key
from app.services.ac_filter import WordHit
from app.services.demo_trace import OptionalLangfuseRun, OptionalLangSmithRun
from common.db.models.llm_log import LLMCallLog
from common.llm.prompts.loader import load_qc_review_prompt
from common.llm.reliability import (
    LLMCircuitOpenError,
    LLMRateLimitError,
    invoke_llm,
    mark_degraded_flag,
    require_real_secret,
    set_default_service,
)
from common.llm.schemas.qc_review import LLMEvidence, LLMHitSchema
from common.llm.transport import message_content_from_completions, post_chat_completions
from common.resilience.timeout import Timeout
from common.security.mask import mask_text
from common.trace.middleware import get_trace_id

VALID_RULE_IDS = frozenset(f"R{i:02d}" for i in range(1, 16))
_AMOUNT_RE = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:元|块|万|圆)?")
_OVERDUE_KEYS = ("due_amount", "overdue_amount", "overdue_days", "ptp_amount")
_DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
InvokeFn = Callable[..., Awaitable[str]]


async def json_object_transport(
    model: str,
    messages: list[dict[str, str]],
    timeout_seconds: float,
) -> str:
    """QC 自有 transport：保留 json_object，使用 invoke_llm 传入的目标 model。"""
    from common.config.settings import settings

    api_key = require_real_secret(
        (settings.LLM_API_KEY or settings.DASHSCOPE_API_KEY or "").strip(),
        label="LLM_API_KEY / DASHSCOPE_API_KEY",
    )
    body = await post_chat_completions(
        base_url=_DASHSCOPE_COMPAT_BASE,
        api_key=api_key,
        model=model,
        messages=messages,
        timeout=float(timeout_seconds),
        response_format={"type": "json_object"},
    )
    content = message_content_from_completions(body)
    if not content:
        raise RuntimeError("LLM returned empty content")
    return content


def _turn_id_of(turn: Any) -> str | None:
    if hasattr(turn, "turn_id"):
        return getattr(turn, "turn_id", None)
    if isinstance(turn, dict):
        return turn.get("turn_id")
    return None


def _turn_speaker(turn: Any) -> str:
    if hasattr(turn, "speaker"):
        return str(getattr(turn, "speaker") or "agent")
    if isinstance(turn, dict):
        return str(turn.get("speaker") or "agent")
    return "agent"


def _turn_text(turn: Any) -> str:
    if hasattr(turn, "text"):
        return str(getattr(turn, "text") or "")
    if isinstance(turn, dict):
        return str(turn.get("text") or "")
    return ""


def _strip_fences(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_turn_id(evidence: LLMEvidence | str, known: set[str]) -> str | None:
    if isinstance(evidence, LLMEvidence):
        return evidence.turn_id if evidence.turn_id in known else None
    blob = evidence or ""
    for tid in known:
        if tid and tid in blob:
            return tid
    return None


def reason_has_fabricated_amount(reason: str, corpus: str) -> bool:
    for match in _AMOUNT_RE.finditer(reason or ""):
        token = match.group().strip()
        digits = re.sub(r"[^\d.]", "", token)
        if not digits:
            continue
        if digits not in corpus and token not in corpus:
            return True
    return False


def build_system_prompt(spec: dict[str, Any]) -> str:
    lines = [
        spec.get("role") or "",
        spec.get("goal") or "",
        "【约束】",
        *[f"- {item}" for item in spec.get("constraints") or []],
        "【禁止】",
        *[f"- {item}" for item in spec.get("prohibitions") or []],
        "只输出 JSON 数组，无命中则 []。",
    ]
    return "\n".join(line for line in lines if line)


def turns_payload(turns: Sequence[Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for turn in turns or []:
        row = {
            "turn_id": str(_turn_id_of(turn) or ""),
            "speaker": _turn_speaker(turn),
            "text": _turn_text(turn),
        }
        for key in _OVERDUE_KEYS:
            row.pop(key, None)
        rows.append(row)
    return rows


class LLMReviewer:
    def __init__(
        self,
        *,
        invoke: InvokeFn | None = None,
        job_store: QcJobStore | None = None,
        publisher: QcEventPublisher | None = None,
        db_session: Any | None = None,
        retry_delay: float = 0.0,
    ) -> None:
        self._invoke = invoke
        self.job_store = job_store
        self.publisher = publisher or QcEventPublisher()
        self.db_session = db_session
        self.retry_delay = retry_delay
        self.degraded = False
        self.call_logs: list[dict[str, Any]] = []
        self.last_langsmith_run_id: str | None = None
        set_default_service("qc")

    async def _as_transport(
        self,
        model: str,
        messages: list[dict[str, str]],
        timeout_seconds: float,
    ) -> str:
        if self._invoke is None:
            return await json_object_transport(model, messages, timeout_seconds)
        try:
            return await self._invoke(model, messages, timeout_seconds)
        except TypeError:
            return await self._invoke(messages)

    def _require_transport_key(self) -> None:
        from common.config.settings import settings

        require_real_secret(
            (settings.LLM_API_KEY or settings.DASHSCOPE_API_KEY or "").strip(),
            label="LLM_API_KEY / DASHSCOPE_API_KEY",
        )

    async def _call_model(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return await invoke_llm(
            model=qc_settings.QC_LLM_MODEL,
            messages=messages,
            fallback_chain=list(qc_settings.QC_LLM_FALLBACK_CHAIN or []),
            timeout=Timeout(total=float(qc_settings.QC_LLM_TIMEOUT)),
            transport=self._as_transport,
        )

    async def _mark_chain_degraded(self) -> None:
        """整链跳过时把配置链每个模型的分片键都置位，health 才能按整链口径为 true。"""
        for model in llm_chain_models():
            await mark_degraded_flag(llm_degraded_key(model))

    async def _clear_model_degraded(self, model: str | None) -> None:
        if not model:
            return
        from redis.exceptions import RedisError

        from common.resilience._redis import get_redis

        try:
            await get_redis().delete(llm_degraded_key(model))
        except (RedisError, OSError, AttributeError):
            return

    async def _on_open(self) -> None:
        self.degraded = True
        await self._mark_chain_degraded()
        await self.publisher.publish_system_alert_raised()

    def _write_log(
        self,
        *,
        prompt: str,
        response: str | None,
        status: str,
        session_id: str | None,
        error_msg: str | None = None,
        confidence: float | None = None,
    ) -> None:
        row = {
            "id": str(uuid4()),
            "trace_id": get_trace_id() or "qc",
            "session_id": session_id,
            "provider": "dashscope",
            "model": qc_settings.QC_LLM_MODEL,
            "prompt": mask_text(prompt),
            "response": mask_text(response) if response else None,
            "status": status,
            "error_msg": error_msg,
            "confidence": confidence,
            "temperature": None,
        }
        self.call_logs.append(row)
        if self.db_session is not None:
            self.db_session.add(
                LLMCallLog(
                    trace_id=row["trace_id"],
                    session_id=session_id,
                    provider="dashscope",
                    model=qc_settings.QC_LLM_MODEL,
                    prompt=row["prompt"],
                    response=row["response"],
                    status=status,
                    error_msg=error_msg,
                    confidence=confidence,
                )
            )

    def _to_hits(
        self,
        parsed: list[LLMHitSchema],
        turns: Sequence[Any],
    ) -> list[WordHit]:
        known = {tid for tid in (_turn_id_of(t) for t in turns) if tid}
        speakers = {_turn_id_of(t): _turn_speaker(t) for t in turns}
        corpus = "\n".join(_turn_text(t) for t in turns)
        hits: list[WordHit] = []
        for item in parsed:
            if item.rule_id not in VALID_RULE_IDS or item.rule_id == "R10":
                continue
            if item.redline_tag == "unverified":
                continue
            turn_id = extract_turn_id(item.evidence, known)
            if turn_id is None:
                continue
            if reason_has_fabricated_amount(item.reason, corpus):
                continue
            speaker = speakers.get(turn_id) or "agent"
            if speaker == "user":
                continue
            evidence_text = (
                item.evidence.text
                if isinstance(item.evidence, LLMEvidence)
                else str(item.evidence)
            )
            hits.append(
                WordHit(
                    speaker="agent",
                    word=item.rule_id,
                    severity=item.level,
                    score_eligible=True,
                    source="llm",
                    redline_tag=item.redline_tag,
                    turn_id=turn_id,
                    evidence_text=mask_text(evidence_text or ""),
                    rule_id=item.rule_id,
                    extras={"confidence": item.confidence, "reason": item.reason},
                )
            )
        return hits

    def _parse_hits(self, raw: str) -> list[LLMHitSchema]:
        text = _strip_fences(raw)
        data = json.loads(text)
        if isinstance(data, dict):
            if "level" in data and "rule_id" in data:
                data = [data]
            else:
                nested = data.get("hits") or data.get("items") or data.get("data")
                data = [] if nested is None else nested
        if not isinstance(data, list):
            raise ValueError("LLM JSON must be an array or object")
        out: list[LLMHitSchema] = []
        for row in data:
            try:
                out.append(LLMHitSchema.model_validate(row))
            except ValidationError:
                continue
        return out

    async def review(
        self,
        turns: Sequence[Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> list[WordHit]:
        self.last_langsmith_run_id = None
        run_inputs = {"session_id": session_id or "", "turn_count": len(turns or [])}
        ls_run = OptionalLangSmithRun(name="qc_llm_review", inputs=run_inputs)
        lf_run = OptionalLangfuseRun("qc_llm_review", run_inputs)
        try:
            hits = await self._review_impl(turns, session_id=session_id)
            outputs = {"hit_count": len(hits), "degraded": self.degraded}
            ls_run.end(outputs=outputs)
            lf_run.end(outputs=outputs)
            return hits
        except Exception as exc:
            ls_run.end(error=str(exc)[:300])
            lf_run.end(error=str(exc)[:300])
            raise
        finally:
            self.last_langsmith_run_id = ls_run.run_id

    async def _review_impl(
        self,
        turns: Sequence[Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> list[WordHit]:
        self.degraded = False
        turns = list(turns or [])
        if not turns:
            return []
        spec = load_qc_review_prompt()
        prompt_version = str(spec.get("version") or "qc_review_v1")
        t0 = time.perf_counter()
        emit_llm_review_started(prompt_version=prompt_version, turn_count=len(turns))
        payload = turns_payload(turns)
        user = json.dumps({"turns": payload}, ensure_ascii=False)
        for banned in _OVERDUE_KEYS:
            if banned in user:
                user = user.replace(banned, "omitted")
        messages = [
            {"role": "system", "content": build_system_prompt(spec)},
            {"role": "user", "content": user},
        ]
        prompt_blob = messages[0]["content"] + "\n" + messages[1]["content"]
        if qc_settings.QC_MOCK_LLM:
            self._write_log(
                prompt=prompt_blob,
                response=None,
                status="qc_review_exemption",
                session_id=session_id,
                error_msg="qc_mock_llm",
            )
            emit_llm_review_completed(
                latency_ms=int((time.perf_counter() - t0) * 1000),
                hit_count=0,
                status="skipped",
            )
            return []
        if self._invoke is None:
            try:
                self._require_transport_key()
            except RuntimeError as exc:
                self._write_log(
                    prompt=prompt_blob,
                    response=None,
                    status="qc_review_exemption",
                    session_id=session_id,
                    error_msg=str(exc)[:300],
                )
                return []
        try:
            result = await self._call_model(messages)
        except (LLMCircuitOpenError, LLMRateLimitError, TimeoutError) as exc:
            reason = "circuit_open"
            if isinstance(exc, LLMRateLimitError):
                reason = "llm_rate_limited"
            elif isinstance(exc, TimeoutError):
                reason = "timeout"
            await self._on_open()
            emit_llm_review_degraded(reason=reason)
            self._write_log(
                prompt=prompt_blob,
                response=None,
                status="qc_review_exemption",
                session_id=session_id,
                error_msg=reason,
            )
            return []
        if result.get("content") is None:
            await self._on_open()
            emit_llm_review_degraded(reason=result.get("error") or "all models failed")
            self._write_log(
                prompt=prompt_blob,
                response=None,
                status="qc_review_exemption",
                session_id=session_id,
                error_msg=str(result.get("error") or "all models failed")[:300],
            )
            return []
        raw = result["content"]
        await self._clear_model_degraded(result.get("model"))
        try:
            parsed = self._parse_hits(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            self._write_log(
                prompt=prompt_blob,
                response=raw,
                status="qc_review_exemption",
                session_id=session_id,
                error_msg="invalid_json",
            )
            return []
        hits = self._to_hits(parsed, turns)
        conf = parsed[0].confidence if parsed else None
        self._write_log(
            prompt=prompt_blob,
            response=raw,
            status="qc_review_exemption",
            session_id=session_id,
            confidence=conf,
        )
        emit_llm_review_completed(
            latency_ms=int((time.perf_counter() - t0) * 1000),
            hit_count=len(hits),
            status="ok",
        )
        return hits

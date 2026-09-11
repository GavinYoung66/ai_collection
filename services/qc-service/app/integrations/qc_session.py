"""QCSession 映射（DD-02 §4.6.1 / §5.2.1）。qc 只消费 DP JSON，禁止反查 cases。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def unwrap_envelope(payload: Any) -> dict[str, Any]:
    """兼容统一信封 {code,data} 与裸 QCSession。"""
    if not isinstance(payload, dict):
        raise TypeError("qc-source payload must be an object")
    if "code" in payload and "data" in payload:
        data = payload.get("data")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise TypeError("envelope data must be an object")
        return data
    return payload


def map_speaker(raw: str | None) -> Literal["agent", "user"]:
    if raw in {"agent", "user"}:
        return raw  # type: ignore[return-value]
    if raw in {"ai", "human"}:
        return "agent"
    return "agent"


class QcTurn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    turn_id: str
    speaker: Literal["agent", "user"]
    text: str
    turn_index: int
    hit_ts: str | None = None
    start_offset_ms: int | None = None

    @field_validator("speaker", mode="before")
    @classmethod
    def _speaker_ai_to_agent(cls, value: Any) -> str:
        if not isinstance(value, str):
            return "agent"
        return map_speaker(value)


class CaseSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    due_amount: float | None = None
    overdue_days: int | None = None
    ptp_amount: Any = None
    strategy_refs: list[Any] = Field(default_factory=list)
    standard_answer_hit: Any = None

    @model_validator(mode="after")
    def _m1_force_nulls(self) -> CaseSnapshot:
        self.ptp_amount = None
        self.standard_answer_hit = None
        if not isinstance(self.strategy_refs, list):
            self.strategy_refs = []
        return self


class DialHistory24h(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phone_masked: str | None = None
    dial_count_24h: int
    window_start: str | None = None
    window_end: str | None = None


class QCSession(BaseModel):
    """离线质检输入。result 必须出键（可为 null）；缺 dial_history_24h 则整段省略。"""

    model_config = ConfigDict(extra="ignore")

    text_ready: bool | None = None
    call_status: str | None = None
    session_id: str
    case_id: str | None = None
    call_id: str | None = None
    call_ended: bool = False
    agent_type: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: int | None = None
    result: str | None = None
    debtor_name: str | None = None
    turns: list[QcTurn] = Field(default_factory=list)
    case_snapshot: CaseSnapshot | None = None
    dial_history_24h: DialHistory24h | None = None
    trace_id: str | None = None

    def to_mapped_dict(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=False)
        if data.get("dial_history_24h") is None:
            data.pop("dial_history_24h", None)
        if data.get("case_snapshot"):
            data["case_snapshot"]["ptp_amount"] = None
            data["case_snapshot"]["standard_answer_hit"] = None
        if "result" not in data:
            data["result"] = None
        return data


def map_qc_source(payload: Any) -> QCSession:
    raw = dict(unwrap_envelope(payload))
    if "result" not in raw:
        raw["result"] = None

    dial = raw.get("dial_history_24h")
    if not isinstance(dial, dict) or "dial_count_24h" not in dial:
        raw.pop("dial_history_24h", None)
        raw["dial_history_24h"] = None

    snap = raw.get("case_snapshot")
    if isinstance(snap, dict):
        raw["case_snapshot"] = {
            **snap,
            "ptp_amount": None,
            "standard_answer_hit": None,
        }

    return QCSession.model_validate(raw)

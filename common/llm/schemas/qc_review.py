"""质检 LLM 语义复核输出 Schema。不做 confidence<0.7 门控。"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field, field_validator

RedlineTag = Literal[
    "threat",
    "fake_identity",
    "stop_call",
    "fake_commit",
    "legal_overstate",
    "unverified",
    "data_fabrication",
    "privacy_leak",
]


class LLMEvidence(BaseModel):
    turn_id: str
    text: str | None = None


class LLMHitSchema(BaseModel):
    level: Literal["high", "medium", "low"]
    rule_id: str
    redline_tag: RedlineTag | None = None
    evidence: Union[LLMEvidence, str]
    reason: str
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("rule_id")
    @classmethod
    def _rule_id_shape(cls, value: str) -> str:
        return (value or "").strip()

"""意图识别 LLM 输出 Pydantic Schema。"""
from enum import Enum

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    """客户意图枚举，对齐 DDL intent_tags.intent_type CHECK IN。"""
    WILLING_TO_PAY = "willing_to_pay"
    EXTENSION_REQUEST = "extension_request"
    FINANCIAL_DIFFICULTY = "financial_difficulty"
    STALLING = "stalling"
    REJECTION = "rejection"
    ANGER = "anger"
    COMPLAINT = "complaint"
    UNCERTAIN = "uncertain"


class IntentResult(BaseModel):
    """LLM 意图识别结构化输出。"""
    intent: IntentType
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，<0.6 标记 uncertain")
    keywords: list[str] = Field(default_factory=list, description="触发意图的关键词")
    reasoning: str = Field(default="", description="推理依据")


INTENT_TYPES = [e.value for e in IntentType]

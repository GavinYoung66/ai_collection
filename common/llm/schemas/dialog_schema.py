"""对话应答 LLM 输出 Pydantic Schema。"""
from pydantic import BaseModel, Field


class DialogResponse(BaseModel):
    """LLM 对话应答结构化输出。"""
    reasoning: str = Field(..., description="推理过程，用于审计，落库时加密存储")
    answer: str = Field(..., min_length=1, max_length=500, description="回复客户的最终话术")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，<0.7 转人工")
    intent: str = Field(
        ...,
        description="识别到的客户意图，取值见 intent_schema.IntentType",
    )
    turn_number: int | None = Field(None, ge=1, description="当前对话轮次")


class DialogTurn(BaseModel):
    """单轮对话上下文，用于构建 prompt。"""
    speaker: str = Field(..., pattern="^(ai|user)$")
    text: str
    turn_number: int = Field(..., ge=1)


class DialogContext(BaseModel):
    """构建对话 prompt 所需的完整上下文。"""
    case_number: str
    overdue_amount: float = Field(..., ge=0)
    overdue_days: int = Field(..., ge=0)
    account_stage: str = Field(..., pattern="^(M0|M1|M2|M3)$")
    history: list[DialogTurn] = Field(default_factory=list, max_length=15)
    customer_intent: str | None = None

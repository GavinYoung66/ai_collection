from pydantic import BaseModel
from typing import Any, Type, TypeVar

T = TypeVar("T", bound=BaseModel)


class LLMProvider:
    async def invoke(
        self,
        prompt: str,
        schema: Type[T],
        trace_id: str,
        session_id: str,
    ) -> T:
        raise NotImplementedError("LLMProvider.invoke() 需要对接实际LLM API实现")


llm_provider = LLMProvider()

"""Mock LLM Provider：鸭子类型对齐 PRD invoke 契约。

common/llm/provider.py 当前为函数式 chat/chat_json，不存在 BaseLLMProvider ABC。
本 Mock 采用鸭子类型实现 invoke，批次3（5.1）真实 Provider 定稿后可再对齐。

契约：async invoke(prompt, schema, trace_id, session_id) -> BaseModel | dict
- 若传入 schema（Pydantic 类），返回 schema 实例
- 否则返回 dict
- 支持 set_delay / set_error_mode / set_seed 注入
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Optional, Type

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# schema 类名 → fixture 前缀映射
_SCHEMA_TO_PREFIX = {
    "CaseEvidenceBundle": "evidence_case",
    "ReflectionReport": "reflection",
    "FactTimeline": "fact_timeline",
    "ComplianceOpinion": "compliance",
    "UserDemandAnalysis": "user_demand",
    "VerdictDraft": "verdict",
    "JudgmentPackage": "generate_package",
}

# prompt 关键词 → fixture 前缀映射（schema 缺失时的备选）
_KEYWORD_TO_PREFIX = [
    (("evidence_case", "案件证据", "案件取证"), "evidence_case"),
    (("evidence_call_qc", "通话质检", "通话证据"), "evidence_call_qc"),
    (("reflection", "反思"), "reflection"),
    (("fact_timeline", "事实还原", "事实时序"), "fact_timeline"),
    (("compliance", "合规"), "compliance"),
    (("user_demand", "用户诉求"), "user_demand"),
    (("verdict", "判责"), "verdict"),
    (("generate_package", "产出物", "研判数据包"), "generate_package"),
]

# 每个前缀的可选 fixture 后缀（按场景）
_SCENARIOS = {
    "evidence_case": ["normal", "multi"],
    "evidence_call_qc": ["normal", "corrupted"],
    "reflection": ["sufficient", "insufficient"],
    "fact_timeline": ["normal", "conflict"],
    "compliance": ["violation", "compliant"],
    "user_demand": ["refund", "emotional"],
    "verdict": ["agent_violation", "user_misunderstanding", "compliant_but_poor_experience", "pending"],
    "generate_package": ["complete", "pending"],
}


class LLMTimeoutError(Exception):
    """模拟 LLM 超时。"""


class LLMRateLimitError(Exception):
    """模拟 LLM 限流。"""


class LLMServerError(Exception):
    """模拟 LLM 服务端错误。"""


class MockLLMProvider:
    """Mock LLM Provider：按 Agent 返回预设 JSON，支持延迟/错误注入/seed 确定性。"""

    def __init__(self, seed: int = 42):
        self._delay: float = 0.0
        self._error_mode: Optional[str] = None  # None | timeout | rate_limit | server_error
        self._seed: int = seed
        self._rng = random.Random(seed)
        self._reasoning_tokens: int = 0  # deepseek-r1 reasoning token 用量计数
        self._call_count: int = 0

    # ── 配置方法 ──

    def set_delay(self, seconds: float) -> None:
        """设置模拟延迟（秒）。"""
        self._delay = max(0.0, seconds)

    def set_error_mode(self, mode: Optional[str]) -> None:
        """设置错误模式：None | timeout | rate_limit | server_error。"""
        if mode is not None and mode not in ("timeout", "rate_limit", "server_error"):
            raise ValueError(f"unsupported error mode: {mode}")
        self._error_mode = mode

    def set_seed(self, seed: int) -> None:
        """重置随机种子，保证 fixture 选择确定性。"""
        self._seed = seed
        self._rng = random.Random(seed)

    @property
    def reasoning_tokens(self) -> int:
        """deepseek-r1 reasoning token 模拟用量。"""
        return self._reasoning_tokens

    @property
    def call_count(self) -> int:
        return self._call_count

    # ── 核心契约 ──

    async def invoke(
        self,
        prompt: str,
        schema: Optional[Type[Any]] = None,
        trace_id: str = "",
        session_id: str = "",
        **_: Any,
    ) -> Any:
        """对齐 ComplaintLLMProvider.invoke 契约（兼容位置参数与 keyword）。

        Args:
            prompt: 渲染后的 prompt 文本
            schema: Pydantic 输出 Schema 类（可选）
            trace_id: 全链路追踪 ID
            session_id: 会话 ID

        Returns:
            schema 实例（若提供）或 dict
        """
        self._call_count += 1

        # 错误注入
        if self._error_mode == "timeout":
            raise LLMTimeoutError(f"LLM timeout (trace_id={trace_id})")
        if self._error_mode == "rate_limit":
            raise LLMRateLimitError(f"LLM rate limit (trace_id={trace_id})")
        if self._error_mode == "server_error":
            raise LLMServerError(f"LLM server error (trace_id={trace_id})")

        # 延迟注入
        if self._delay > 0:
            await asyncio.sleep(self._delay)

        # 选择 fixture
        fixture_data = self._load_fixture(prompt, schema)

        # deepseek-r1 reasoning token 模拟（仅当 prompt 含合规/判责时计入）
        if schema is not None and schema.__name__ in ("ComplianceOpinion", "VerdictDraft"):
            self._reasoning_tokens += self._rng.randint(200, 800)

        # 构造返回
        if schema is not None:
            return schema(**fixture_data)
        return fixture_data

    # ── 内部方法 ──

    def _load_fixture(self, prompt: str, schema: Optional[Type[Any]]) -> dict:
        prefix = self._resolve_prefix(prompt, schema)
        scenarios = _SCENARIOS.get(prefix, ["normal"])
        scenario = self._rng.choice(scenarios)
        fixture_path = _FIXTURES_DIR / f"{prefix}_{scenario}.json"
        if not fixture_path.exists():
            # 回退到第一个可用场景
            fixture_path = _FIXTURES_DIR / f"{prefix}_{scenarios[0]}.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _resolve_prefix(self, prompt: str, schema: Optional[Type[Any]]) -> str:
        if schema is not None:
            prefix = _SCHEMA_TO_PREFIX.get(schema.__name__)
            if prefix:
                return prefix
        prompt_lower = prompt.lower()
        for keywords, prefix in _KEYWORD_TO_PREFIX:
            if any(kw.lower() in prompt_lower for kw in keywords):
                return prefix
        raise ValueError(
            f"cannot resolve fixture for prompt={prompt[:80]!r} schema={schema}"
        )

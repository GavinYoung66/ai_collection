"""LLM Provider 抽象基类 + 共享 Prompt 组装（platform prompt-ops 统一入口）。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import uuid4

from jinja2 import Template

from common.prompt.ab import configure_ab, get_ab_config, select_version
from common.prompt.loader import load_prompt_sections
from common.prompt.telemetry import prompt_metadata

_RAG_PROMPT_ID = "rag_answer"
_RAG_PROMPT_BASE_VERSION = "1.0.0"

# A/B 配置为进程内存态：未配置时默认 100% 走基线版本，避免调用点 KeyError（对齐 qc 先例）。
if get_ab_config(_RAG_PROMPT_ID) is None:
    configure_ab(_RAG_PROMPT_ID, {_RAG_PROMPT_BASE_VERSION: 100})


def prompt_metadata_for(trace_key: str) -> dict[str, str]:
    """返回本次调用实际使用的 prompt_id@version（供 Langfuse span metadata）。

    A/B 选择对同一 trace_key 恒定，故与 build_answer_prompt(trace_key=...) 选中的版本一致。
    """
    return prompt_metadata(_RAG_PROMPT_ID, select_version(_RAG_PROMPT_ID, trace_key))


def build_answer_prompt(
    context: list[dict], query: str, *, trace_key: str | None = None
) -> tuple[str, str]:
    """按模板组装回答 Prompt（common/prompt/loader 唯一入口，禁止硬编码明文）。

    context 项：{"content": str, "page": int}。返回 (system, user)。
    system 取 sections.role（原 system 全文），user 由 input_constraint（含
    {{ query }}/{{ context }} 占位符）与 output_schema 两段经 Jinja2 渲染后拼接，
    与迁移前 build_answer_prompt 输出逐字一致（baseline 零漂移）。
    页码标注仅在片段携带 page（chunk 级无页码数据源时恒缺省）才生成，
    避免回答虚构 "[0页]" 误导。
    trace_key：A/B 分流键（调用方传 trace_id，保证与 metadata 版本一致）；
    缺省时按请求随机，保持原行为。
    """
    prompt_version = select_version(_RAG_PROMPT_ID, trace_key or f"rag-anon-{uuid4().hex}")
    spec = load_prompt_sections(
        f"{_RAG_PROMPT_ID}@{prompt_version}", service="rag-service"
    )
    sections = spec["sections"]

    def _fmt(c: dict) -> str:
        if c.get("page"):
            return f"[{c['page']}页] {c['content']}"
        return c["content"]

    context_text = "\n".join(_fmt(c) for c in context)
    system = sections["role"]
    user = "\n\n".join(
        Template(sections[name]).render(query=query, context=context_text)
        for name in ("input_constraint", "output_schema")
    )
    return system, user


@dataclass
class LLMAnswer:
    """LLM 回答结果。

    citations: 命中文档 doc_id 列表（取注入上下文去重后的 doc_id，
    回答文本基于召回片段组装并标注页码，符合"仅依据给定资料"约束）。
    """

    text: str
    citations: list[str] = field(default_factory=list)


class LLMProvider(ABC):
    """在线大模型回答提供方抽象基类。

    所有实现（dashscope / local OpenAI 兼容）需实现 `generate`，
    基于召回片段生成回答；切换 LLM_PROVIDER 不影响对外请求/响应契约。
    """

    @abstractmethod
    async def generate(
        self, context: list[dict], query: str, timeout: float
    ) -> LLMAnswer:
        """基于召回片段生成回答。

        Args:
            context: 注入的召回片段（脱敏后），项含 content/page/doc_id。
            query: 用户原始提问。
            timeout: 单次调用超时（秒）。

        Returns:
            LLMAnswer(text, citations)。

        Raises:
            RuntimeError: 服务处于熔断状态或未配置密钥/基址。
            httpx.HTTPError: 调用持续失败（已耗尽重试次数）。
        """
        ...

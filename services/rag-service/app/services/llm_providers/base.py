"""LLM Provider 抽象基类 + 共享 Prompt 组装（V5，design D8）。"""
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# 路径：services/rag-service/conf/llm_answer_prompt.yaml（Docker 中 COPY 至 /app/conf/）
_PROMPT_FILE = (
    Path(__file__).resolve().parents[3] / "conf" / "llm_answer_prompt.yaml"
)

_prompt_cache: dict | None = None
_prompt_lock = threading.Lock()


def _load_prompt_template() -> dict:
    """加载 conf/llm_answer_prompt.yaml（进程内缓存；缺失/解析失败按不可用抛错）。"""
    global _prompt_cache
    if _prompt_cache is None:
        with _prompt_lock:
            if _prompt_cache is None:
                try:
                    with open(_PROMPT_FILE, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                except OSError as e:
                    raise RuntimeError(f"llm_answer_prompt.yaml 读取失败: {e}") from e
                for placeholder in ("{query}", "{context}"):
                    if placeholder not in (data.get("system", "") + data.get("user", "")):
                        raise RuntimeError(
                            f"llm_answer_prompt.yaml 缺少占位符 {placeholder}"
                        )
                _prompt_cache = data
    return _prompt_cache


def build_answer_prompt(context: list[dict], query: str) -> tuple[str, str]:
    """按模板组装回答 Prompt（禁止代码硬编码明文）。

    context 项：{"content": str, "page": int}。返回 (system, user)。
    页码标注仅在片段携带 page（chunk 级无页码数据源时恒缺省）才生成，
    避免回答虚构 "[0页]" 误导。
    """
    data = _load_prompt_template()

    def _fmt(c: dict) -> str:
        if c.get("page"):
            return f"[{c['page']}页] {c['content']}"
        return c["content"]

    context_text = "\n".join(_fmt(c) for c in context)
    return (
        data["system"],
        data["user"].replace("{query}", query).replace("{context}", context_text),
    )


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

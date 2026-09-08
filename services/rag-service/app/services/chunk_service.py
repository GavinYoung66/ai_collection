"""文本分块：Unicode 归一化 + 章节边界切分 + 语义边界切分。"""
import re
import unicodedata

import structlog

from app.settings import settings

logger = structlog.get_logger()

_SENTENCE_END_RE = re.compile(r"(?<=[。！？.!?；;])\s*")
# 数字小节（5.2）、中文序号（五、）、第X章
_SECTION_SPLIT_RE = re.compile(
    r"(?<=\n)(?=\d+\.\d+[\s\u3001]?|[第][一二三四五六七八九十百千]+[、.]|[一二三四五六七八九十]+[、.])"
)


def normalize_text(text: str) -> str:
    """NFKC 归一化：PDF 兼容字符（如 录⼊→录入）统一为检索友好形式。"""
    return unicodedata.normalize("NFKC", text or "")


class ChunkService:
    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        min_length: int | None = None,
    ):
        self.chunk_size = chunk_size or settings.KB_CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or settings.KB_CHUNK_OVERLAP
        self.min_length = min_length or settings.KB_CHUNK_MIN_LENGTH

    def clean(self, text: str) -> str:
        text = normalize_text(text)
        lines = text.split("\n")
        cleaned_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped and stripped not in ("视图",):
                cleaned_lines.append(stripped)
        text = "\n".join(cleaned_lines)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _split_by_sections(self, text: str) -> list[str]:
        """按章节标题切分，避免 5.2 流程图与 5.3 配图混在同一 chunk。"""
        parts = _SECTION_SPLIT_RE.split(text)
        if len(parts) <= 1:
            return [text]
        return [p.strip() for p in parts if p.strip()]

    def _split_section_body(self, section: str) -> list[str]:
        """对单个章节内按段落/句子继续切分。"""
        paragraphs = [p.strip() for p in section.split("\n\n") if p.strip()]
        chunks: list[str] = []
        for para in paragraphs:
            if len(para) <= self.chunk_size:
                chunks.append(para)
            else:
                sentences = _SENTENCE_END_RE.split(para)
                current = ""
                for sent in sentences:
                    if not sent.strip():
                        continue
                    if len(current) + len(sent) <= self.chunk_size:
                        current += sent
                    else:
                        if current:
                            chunks.append(current)
                        if len(sent) <= self.chunk_size:
                            current = sent
                        else:
                            step = max(self.chunk_size - self.chunk_overlap, 1)
                            for i in range(0, len(sent), step):
                                piece = sent[i : i + self.chunk_size]
                                if piece:
                                    chunks.append(piece)
                            current = ""
                if current:
                    chunks.append(current)
        return chunks

    def split(self, text: str, category: str) -> list[dict]:
        sections = self._split_by_sections(text)
        merged_chunks: list[str] = []
        for section in sections:
            merged_chunks.extend(self._split_section_body(section))

        result: list[dict] = []
        result_idx = 0
        for chunk_text in merged_chunks:
            if len(chunk_text.strip()) < self.min_length:
                continue
            result.append({
                "content": chunk_text.strip(),
                "chunk_index": result_idx,
                "category": category,
            })
            result_idx += 1
        return result

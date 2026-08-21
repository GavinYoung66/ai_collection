"""文本分块：按语义边界切分 + 脏数据过滤。"""
import re

import structlog

from common.config.settings import settings

logger = structlog.get_logger()

_SENTENCE_END_RE = re.compile(r"(?<=[。！？.!?；;])\s*")


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
        lines = text.split("\n")
        cleaned_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                cleaned_lines.append(stripped)
        text = "\n".join(cleaned_lines)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def split(self, text: str, category: str) -> list[dict]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

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
                            for i in range(0, len(sent), self.chunk_size - self.chunk_overlap):
                                piece = sent[i:i + self.chunk_size]
                                if piece:
                                    chunks.append(piece)
                            current = ""
                if current:
                    chunks.append(current)

        result: list[dict] = []
        result_idx = 0
        for chunk_text in chunks:
            if len(chunk_text.strip()) < self.min_length:
                continue
            result.append({
                "content": chunk_text.strip(),
                "chunk_index": result_idx,
                "category": category,
            })
            result_idx += 1
        return result

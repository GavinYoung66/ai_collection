"""文档解析器抽象基类。"""
from abc import ABC, abstractmethod
from pathlib import Path


class DocumentParser(ABC):
    @abstractmethod
    async def extract_text(self, file_path: Path) -> str:
        """提取纯文本，返回原始文本（清洗由 ChunkService 处理）。"""
        ...

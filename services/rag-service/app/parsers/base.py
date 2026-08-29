"""文档解析器抽象基类。"""
from abc import ABC, abstractmethod
from pathlib import Path


class DocumentParser(ABC):
    @abstractmethod
    async def extract_text(self, file_path: Path) -> str:
        """提取纯文本，返回原始文本（清洗由 ChunkService 处理）。"""
        ...

    async def extract_with_assets(self, file_path: Path) -> tuple[str, list]:
        """提取文本 + 内嵌资产列表（V5 多模态可选能力，design D2/D4/D5）。

        默认实现委托 extract_text() 返回 (text, [])；需要内嵌图片/表格抽取的
        解析器（PDF/DOCX/PPTX 等）覆写此方法返回资产元数据列表。既有
        extract_text 签名与全部既有 Parser 实现零改动。
        """
        text = await self.extract_text(file_path)
        return text, []

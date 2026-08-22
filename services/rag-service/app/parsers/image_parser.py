"""图片解析器：通过 OCR 服务提取图片文字。"""
from pathlib import Path

from app.parsers.base import DocumentParser


class ImageParser(DocumentParser):
    """PNG/JPG 图片解析，依赖 OCRClient 调用 PaddleOCR 独立服务。"""

    def __init__(self, ocr_client=None):
        self._ocr_client = ocr_client

    async def extract_text(self, file_path: Path) -> str:
        """读取图片 bytes，调用 OCRClient.recognize 返回文本。"""
        file_bytes = file_path.read_bytes()
        if self._ocr_client is None:
            # 延迟初始化：生产环境由 DI 注入，测试可直接传入 mock
            from app.api.deps import get_ocr_client
            self._ocr_client = await get_ocr_client()
        return await self._ocr_client.recognize(file_bytes, file_path.name)

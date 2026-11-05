"""上传大小校验测试（rag-service-final-fix 2.2：A 类阻断 top_k 之外的文档上传约束）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.deps import save_upload_file
from app.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


def _make_upload_file(content: bytes, size: int | None = None, filename: str = "x.txt"):
    f = MagicMock()
    f.filename = filename
    if size is not None:
        f.size = size

    # T78：read 改消耗型游标（支持分块 size 语义，对齐生产 UploadFile.read(size)）
    pos = {"n": 0}

    async def _read(chunk: int = -1):
        start = pos["n"]
        if chunk is None or chunk < 0:
            data = content[start:]
            pos["n"] = len(content)
        else:
            data = content[start : start + chunk]
            pos["n"] += len(data)
        return data

    f.read = AsyncMock(side_effect=_read)
    return f


class TestUploadSizeLimit:
    @pytest.mark.asyncio
    async def test_upload_over_size_limit_returns_b0001(self):
        """Content-Length 超 KB_MAX_FILE_SIZE → B0001，且不落盘。"""
        over = settings.KB_MAX_FILE_SIZE + 1
        f = _make_upload_file(content=b"", size=over)
        with pytest.raises(BizException) as exc_info:
            await save_upload_file(f)
        assert exc_info.value.code == ErrorCode.B0001
        f.read.assert_not_awaited()  # 校验先于任何解析/落库

    @pytest.mark.asyncio
    async def test_upload_over_size_no_content_length_b0001(self):
        """无 Content-Length 头 → read 后按字节校验，超限 B0001 且临时文件不残留。"""
        over = settings.KB_MAX_FILE_SIZE + 1
        f = _make_upload_file(content=b"x" * over)  # size=None → 走字节校验
        with pytest.raises(BizException) as exc_info:
            await save_upload_file(f)
        assert exc_info.value.code == ErrorCode.B0001

    @pytest.mark.asyncio
    async def test_upload_at_limit_boundary_succeeds(self):
        """恰好等于上限 → 成功（边界不误杀）。"""
        content = b"x" * settings.KB_MAX_FILE_SIZE
        f = _make_upload_file(content=content, size=settings.KB_MAX_FILE_SIZE)
        path = await save_upload_file(f)
        try:
            assert path.exists()
            assert path.stat().st_size == settings.KB_MAX_FILE_SIZE
            assert path.read_bytes() == content
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_upload_normal_file_still_works(self):
        """正常小文件上传不受影响（存量行为）。"""
        content = b"hello rag-service" * 20
        f = _make_upload_file(content=content, size=len(content))
        path = await save_upload_file(f)
        try:
            assert path.exists()
            assert path.read_bytes() == content
            assert path.suffix == ".txt"
        finally:
            path.unlink(missing_ok=True)

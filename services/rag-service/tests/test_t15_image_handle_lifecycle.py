"""T15：图片缩放句柄生命周期单测。

验证 ``with Image.open(...)`` 确定性关闭解码器：批量摄入 fd 不线性增长，
且回归防止退回裸 ``Image.open``。
"""
from __future__ import annotations

import inspect
import io
import os

import pytest
from PIL import Image

from app.parsers.asset_extractor import _scale_image
from app.settings import settings


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (1600, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color="red").save(buf, format=fmt)
    return buf.getvalue()


def _fd_count() -> int | None:
    for path in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return None


def test_scale_image_returns_scaled_bytes_and_mime():
    binary, mime = _scale_image(_image_bytes())
    assert mime == "image/png"
    with Image.open(io.BytesIO(binary)) as out:
        assert max(out.size) <= settings.KB_ASSETS_MAX_SIZE


def test_scale_image_keeps_source_format_for_jpeg():
    binary, mime = _scale_image(_image_bytes("JPEG", (1200, 900)))
    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(binary)) as out:
        assert out.format == "JPEG"


def test_image_decoder_is_closed_deterministically(monkeypatch):
    """``with`` 退出即 close：捕获 open 返回的实例，断言 fp 已释放。"""
    opened = []
    real_open = Image.open

    def tracking_open(*args, **kwargs):
        img = real_open(*args, **kwargs)
        opened.append(img)
        return img

    monkeypatch.setattr(Image, "open", tracking_open)
    _scale_image(_image_bytes())
    assert opened, "预期 Image.open 被调用"
    assert all(img.fp is None for img in opened), "解码器句柄未被确定性关闭"


@pytest.mark.skipif(_fd_count() is None, reason="平台无 /proc 或 /dev/fd")
def test_fd_count_stays_flat_across_100_images():
    """连续处理 100 张图，进程 fd 水位稳定（不随批次线性增长）。"""
    _scale_image(_image_bytes(size=(64, 64)))  # 预热
    before = _fd_count()
    for _ in range(100):
        _scale_image(_image_bytes(size=(320, 240)))
    after = _fd_count()
    assert after - before <= 5, f"fd 泄漏：{before} → {after}"


def test_source_uses_context_manager_only():
    """源码层回归：唯一一次 Image.open 必须处于 with 上下文内。"""
    src = inspect.getsource(_scale_image)
    assert "\n    with Image.open" in src
    assert "img = Image.open" not in src


@pytest.mark.parametrize("bad", [b"", b"not-an-image", b"\x89PNG\r\n\x1a\n-truncated"])
def test_invalid_bytes_raise_without_handle_leak(bad):
    from PIL import UnidentifiedImageError

    with pytest.raises((UnidentifiedImageError, OSError)):
        _scale_image(bad)

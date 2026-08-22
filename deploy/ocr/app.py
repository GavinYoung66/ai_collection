"""PaddleOCR 独立服务：FastAPI 暴露 /ocr 与 /health，启动时预热规避首请求 10s+ 模型加载。"""
from __future__ import annotations

import io
import logging

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from paddleocr import PaddleOCR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ocr-service")

app = FastAPI(title="ocr-service")

# 按 lang 缓存 PaddleOCR 实例，避免每请求重复加载模型
_ocr_cache: dict[str, PaddleOCR] = {}


def _get_ocr(lang: str) -> PaddleOCR:
    """按 lang 获取或创建 PaddleOCR 实例（带缓存）。"""
    if lang not in _ocr_cache:
        _ocr_cache[lang] = PaddleOCR(use_angle_cls=True, lang=lang)
    return _ocr_cache[lang]


@app.on_event("startup")
def _preheat() -> None:
    """启动时预加载默认 lang=ch 模型并跑一次空白图推理，规避首请求冷启动。"""
    ocr = _get_ocr("ch")
    blank = Image.new("RGB", (64, 64), (255, 255, 255))
    ocr.ocr(np.array(blank), cls=True)
    logger.info("ocr_preheat_done lang=ch")


@app.get("/health")
def health() -> dict:
    """健康检查：返回 200 ok。"""
    return {"status": "ok"}


@app.post("/ocr")
async def ocr_endpoint(
    file: UploadFile = File(...),
    lang: str = Form("ch"),
) -> dict:
    """OCR 识别入口，接受 form-data file+lang，返回拼接文本与版面布局。

    Args:
        file: 上传的图片文件（PNG/JPG 等 PIL 可解码格式）。
        lang: PaddleOCR 语言参数，默认 ch（中英文混合）。

    Returns:
        {"text": str, "layout": [{"bbox": [x1, y1, x2, y2], "text": str, "score": float}]}
    """
    try:
        raw = await file.read()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        logger.exception("ocr_image_decode_failed filename=%s", file.filename)
        raise HTTPException(status_code=400, detail="image_decode_failed")

    try:
        ocr = _get_ocr(lang)
        result = ocr.ocr(np.array(image), cls=True)
    except Exception:
        logger.exception("ocr_inference_failed filename=%s lang=%s", file.filename, lang)
        raise HTTPException(status_code=500, detail="ocr_inference_failed")

    # PaddleOCR 返回结构：[page][line] = [bbox(4 点), (text, score)]
    layout: list[dict] = []
    text_parts: list[str] = []
    for page in result or []:
        for line in page or []:
            bbox, (text, score) = line
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            layout.append({
                "bbox": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))],
                "text": text,
                "score": float(score),
            })
            text_parts.append(text)

    return {"text": "\n".join(text_parts), "layout": layout}

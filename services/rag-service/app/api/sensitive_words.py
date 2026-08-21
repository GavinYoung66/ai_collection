"""敏感词只读接口：GET /sensitive-words/current.dict（internal，只读）。

- 读取项目内置敏感词字典文件，按原始文本原样返回，Content-Type: text/plain。
- 字典文件不存在 / IO 读取失败 → HTTP 404，绝不抛 500、不触发全局异常。
- 不做敏感词拦截 / 过滤 / 校验，仅词典读取输出。
- 内部所有异常均就地捕获，只影响本接口，不影响其他接口。
"""
import asyncio
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

logger = structlog.get_logger()

router = APIRouter(tags=["sensitive-words"])

# 项目内置敏感词字典文件（基于模块位置解析，不依赖 settings/DB）
DICT_FILE = Path(__file__).resolve().parents[2] / "data" / "sensitive_words.dict"


async def _load_dict_text() -> str:
    """读取字典文件原文；不存在/读取失败统一视为文件不可用。"""
    try:
        return await asyncio.to_thread(DICT_FILE.read_text, encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="敏感词字典文件不存在")
    except Exception as e:  # IOError/编码等一切异常 → 404，不冒泡到全局
        logger.warning("sensitive_dict_load_failed", path=str(DICT_FILE), error=str(e))
        raise HTTPException(status_code=404, detail="敏感词字典文件不存在")


@router.get(
    "/sensitive-words/current.dict",
    name="sensitive_words_current_dict",
    summary="敏感词字典（内部只读）",
    description="INTERNAL：原样返回项目内置敏感词字典文本；字典缺失/读取失败返回 404。",
)
async def current_dict():
    """只读接口：原样返回字典文本，不包装 JSON。"""
    return PlainTextResponse(await _load_dict_text())

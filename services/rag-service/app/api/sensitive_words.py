"""敏感词词库接口：GET /sensitive-words/current.dict（内部只读）+ POST /sensitive-words/refresh（管理员刷新）。

- current.dict：优先 DB 活跃词（≥50 阈值），不足/异常回退内置字典文件；均失败 → 404；
- refresh：DB 阈值校验 → Redis 版本号自增 → Pub/Sub 广播失效通知。
- 不做敏感词拦截 / 过滤 / 校验，仅词典读取与版本同步。
- 内部异常就地捕获，只影响本接口，不影响其他接口。
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pathlib import Path
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, success_response
from app.services.rate_limit import rate_limiter
from app.services.require_role import require_roles
from app.services.sensitive_words_sync import SensitiveWordsSyncService

logger = structlog.get_logger()

router = APIRouter(tags=["sensitive-words"])

# 项目内置敏感词字典兜底文件（V1 兼容：测试通过 monkeypatch 替换此路径）
DICT_FILE = Path(__file__).resolve().parents[2] / "data" / "sensitive_words.dict"


@router.get(
    "/sensitive-words/current.dict",
    name="sensitive_words_current_dict",
    summary="敏感词字典（内部只读）",
    description="INTERNAL：优先返回 DB 活跃词（≥50 阈值），不足/异常回退内置字典文本；均失败返回 404。",
)
async def current_dict(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """只读接口：返回当前生效敏感词字典文本，不包装 JSON。"""
    try:
        dict_text = await SensitiveWordsSyncService(db, redis, dict_file=DICT_FILE).get_current_dict()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="敏感词字典文件不存在")
    except Exception as e:  # DB/IO 等一切异常 → 404，不冒泡到全局
        logger.warning("sensitive_dict_load_failed", error=str(e))
        raise HTTPException(status_code=404, detail="敏感词字典文件不存在")
    return PlainTextResponse(content=dict_text)


@router.post(
    "/sensitive-words/refresh",
    name="sensitive_words_refresh",
    summary="敏感词词库刷新（管理员）",
    description="DB 阈值校验通过后自增 Redis 版本号并广播 Pub/Sub 失效通知。",
)
async def refresh_dict(
    auth: None = Depends(require_roles("ADMIN", "SUPERVISOR")),
    rate: None = Depends(rate_limiter("sensitive_refresh", 10)),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """刷新敏感词版本号并广播失效通知，返回新版本号。"""
    new_version = await SensitiveWordsSyncService(db, redis).refresh()
    return success_response({"version": new_version})

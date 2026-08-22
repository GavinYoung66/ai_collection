"""敏感词词库同步：DB 优先 + 文件兜底，版本号发布到 Redis Pub/Sub。

- get_current_dict：DB 活跃词（≥50 阈值）→ 拼接文本；不足或异常 → 回退内置字典文件；
  两者均失败 → 抛 FileNotFoundError（路由层转 404）。
- refresh：DB 阈值校验 → Redis INCR 版本号 → PUBLISH kb.sensitive_updated；
  DB/INCR 失败抛 BizException，PUBLISH 失败仅 warning 不阻断。
"""
import asyncio
import json
import time
from pathlib import Path

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import SensitiveWord
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

# 项目内置敏感词字典兜底文件（基于模块位置解析，不依赖 settings/DB）
_DICT_FILE = Path(__file__).resolve().parents[2] / "data" / "sensitive_words.dict"

_VERSION_KEY = "rag:sensitive_words:version"
_PUBLISH_CHANNEL = "kb.sensitive_updated"
_MIN_DICT_SIZE = 50


class SensitiveWordsSyncService:
    """敏感词同步服务：DB 活跃词读取（≥50 阈值校验）+ 文件兜底 + 版本号广播。"""

    def __init__(self, db: AsyncSession, redis: Redis, dict_file: Path | None = None):
        self.db = db
        self.redis = redis
        self._dict_file = dict_file or _DICT_FILE

    async def get_current_dict(self) -> str:
        """获取当前生效敏感词字典文本。

        - DB 路径：SELECT word FROM sensitive_words WHERE status='active' AND is_delete='0' ORDER BY word；
          活跃词数 ≥ 50 → 返回 '\\n'.join(sorted(words))；
        - 活跃词数 < 50 或 DB 异常：回退读取 data/sensitive_words.dict；
        - 两者均失败：抛 FileNotFoundError（由调用方转 404）。
        """
        # DB 路径
        try:
            stmt = (
                select(SensitiveWord.word)
                .where(
                    SensitiveWord.status == "active",
                    SensitiveWord.is_delete == "0",
                )
                .order_by(SensitiveWord.word)
            )
            result = await self.db.execute(stmt)
            words = result.scalars().all()
            if len(words) >= _MIN_DICT_SIZE:
                return "\n".join(sorted(words))
            logger.info(
                "sensitive_dict_db_below_threshold", count=len(words)
            )
        except Exception as e:
            logger.warning("sensitive_dict_db_failed", error=str(e))

        # 文件兜底
        try:
            return await asyncio.to_thread(self._dict_file.read_text, encoding="utf-8")
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.warning(
                "sensitive_dict_file_failed",
                path=str(self._dict_file),
                error=str(e),
            )
            raise FileNotFoundError(f"敏感词字典文件不可用: {e}")

    async def refresh(self) -> int:
        """刷新敏感词版本号并广播失效通知。

        - Step1: DB 校验 status='active' 活跃词数 ≥ 50，不足抛 BizException(A3001)；
        - Step2: Redis INCR rag:sensitive_words:version → new_version；
        - Step3: Redis PUBLISH kb.sensitive_updated {version, trace_id, ts}；
        - DB 异常 / INCR 异常 → BizException(B0007)；PUBLISH 失败仅 warning，不阻断；
        - 返回 new_version。
        """
        # Step1: DB 阈值校验
        try:
            stmt = select(SensitiveWord.word).where(
                SensitiveWord.status == "active",
                SensitiveWord.is_delete == "0",
            )
            result = await self.db.execute(stmt)
            words = result.scalars().all()
            if len(words) < _MIN_DICT_SIZE:
                logger.warning(
                    "sensitive_refresh_below_threshold", count=len(words)
                )
                raise BizException(
                    ErrorCode.A3001,
                    f"活跃敏感词数量不足: {len(words)}<{_MIN_DICT_SIZE}",
                )
        except BizException:
            raise
        except Exception as e:
            logger.error("sensitive_refresh_db_failed", error=str(e))
            raise BizException(ErrorCode.B0007, f"敏感词词库校验失败: {e}")

        # Step2: Redis INCR 版本号
        try:
            new_version = await self.redis.incr(_VERSION_KEY)
        except Exception as e:
            logger.error("sensitive_refresh_incr_failed", error=str(e))
            raise BizException(ErrorCode.B0007, f"敏感词版本号自增失败: {e}")

        # Step3: Redis PUBLISH 失效广播（失败仅 warning，不阻断）
        trace_id = get_trace_id() or "startup"
        payload = json.dumps(
            {
                "version": new_version,
                "trace_id": trace_id,
                "ts": int(time.time()),
            },
            ensure_ascii=False,
        )
        try:
            await self.redis.publish(_PUBLISH_CHANNEL, payload)
        except Exception as e:
            logger.warning(
                "sensitive_refresh_publish_failed",
                version=new_version,
                error=str(e),
            )

        logger.info(
            "sensitive_refreshed",
            version=new_version,
            words=len(words),
            trace_id=trace_id,
        )
        return new_version

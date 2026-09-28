"""敏感词词库同步：DB 优先 + 文件兜底，版本号写 Redis，通知经 EventBus。

- get_current_dict：DB 活跃词（≥50 阈值）→ 拼接文本；不足或异常 → 回退内置字典文件；
  两者均失败 → 抛 FileNotFoundError（路由层转 404）。
- refresh：DB 阈值校验 → Redis INCR 版本号 → EventBus 发布 kb.sensitive_updated；
  DB/INCR 失败抛 BizException，总线发布失败仅 warning 不阻断（version 轮询兜底）。
"""
from __future__ import annotations

import asyncio
import os
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
_EVENT_NAME = "kb.sensitive_updated"
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
        - Step3: EventBus 发布 kb.sensitive_updated（data 可空；version 不进信封 data）；
        - DB 异常 / INCR 异常 → BizException(B0007)；总线发布失败仅 warning，不阻断；
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

        # Step3: EventBus 失效广播（失败仅 warning，不阻断）
        trace_id = get_trace_id() or "startup"
        await self.publish_version(self.redis, new_version, trace_id)

        logger.info(
            "sensitive_refreshed",
            version=new_version,
            words=len(words),
            trace_id=trace_id,
        )
        return new_version

    @staticmethod
    async def publish_version(redis: Redis, version: int, trace_id: str) -> None:
        """经 EventBus 发布 kb.sensitive_updated（失败仅 warning，不阻断）。

        ``redis`` 参数保留以兼容 startup seed 调用方；版本号已写 Redis 键，
        信封 ``data`` 故意为空（version 不进非白名单 data 字段）。
        """
        del redis  # version lives in Redis key; not used for bus publish
        try:
            if not (os.getenv("EVENT_BUS") or "").strip():
                os.environ["EVENT_BUS"] = "rocketmq"
            if not (os.getenv("EVENT_BUS_SVC") or "").strip():
                os.environ["EVENT_BUS_SVC"] = "rag"
            from common.eventbus.factory import get_event_bus

            bus = get_event_bus()
            await bus.publish(
                _EVENT_NAME,
                {},
                key=f"sensitive-v{version}",
                trace_id=trace_id,
            )
        except Exception as e:
            logger.warning(
                "sensitive_publish_version_failed",
                version=version,
                error=str(e),
            )

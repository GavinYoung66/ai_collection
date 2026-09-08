"""complaint-service Redis Key 模式（PRD 9.3）。

- DB：6（投诉研判专用）
- 前缀：csmp:
- 复用 common Redis 连接，不在本服务新建连接池。
"""
from __future__ import annotations

PREFIX = "csmp:"
DB = 6

# ── TTL 常量（秒），对齐 PRD 9.3 ──
TTL_SESSION = 86400            # 24h —— session 状态
TTL_TICKET_PKG = 86400         # 24h —— 取证包
TTL_VERDICT_LATEST = 86400     # 24h —— 最新判责
TTL_RSLV_LATEST = 86400        # 24h —— 最新处置方案
TTL_HOTSPOTS = 21600           # 6h —— 统计热点
TTL_GATHER_LOCK = 120          # 2min —— 取证幂等锁
TTL_TASK_TIMEOUT = 1800        # 30min —— 任务超时标记
TTL_COMPLAINT_LOCK = 1800      # 30min —— 工单接入幂等锁（对齐 step8 9.5）
TTL_SESSION_FALLBACK = 259200  # 72h —— Redis 降级 checkpoint
TTL_LLM_DEGRADED: int | None = None   # 永不过期 —— LLM 熔断标识
TTL_MODE_ACTIVE: int | None = None    # 永不过期 —— 当前生效模式


# ── 1. LangGraph State 持久化缓存 ──
def session_key(session_id: str) -> str:
    """csmp:session:{session_id} —— STRING(JSON)，TTL 24h。"""
    return f"{PREFIX}session:{session_id}"


# ── 2. 取证包缓存 ──
def ticket_pkg_key(ticket_id: str) -> str:
    """csmp:ticket:{id}:pkg —— STRING(JSON)，TTL 24h。"""
    return f"{PREFIX}ticket:{ticket_id}:pkg"


# ── 3. 最新判责缓存 ──
def verdict_latest_key(complaint_id: str) -> str:
    """csmp:verdict:{id}:latest —— STRING(JSON)，TTL 24h。"""
    return f"{PREFIX}verdict:{complaint_id}:latest"


# ── 4. 处置方案缓存 ──
def rslv_latest_key(complaint_id: str) -> str:
    """csmp:rslv:{id}:latest —— STRING(JSON)，TTL 24h。"""
    return f"{PREFIX}rslv:{complaint_id}:latest"


# ── 5. 统计热点缓存 ──
def hotspots_key(start: str, end: str) -> str:
    """csmp:hotspots:{start}:{end} —— STRING(JSON)，TTL 6h。"""
    return f"{PREFIX}hotspots:{start}:{end}"


# ── 6. 取证幂等锁 ──
def gather_lock_key(complaint_id: str) -> str:
    """csmp:gather:lock:{id} —— STRING(SET NX EX)，TTL 120s。"""
    return f"{PREFIX}gather:lock:{complaint_id}"


# ── 7. LLM 熔断标识 ──
LLM_DEGRADED_KEY = f"{PREFIX}llm_degraded"  # 永不过期


# ── 8. 全局任务超时标记 ──
def task_timeout_key(session_id: str) -> str:
    """csmp:task:timeout:{session_id} —— STRING，TTL 30min。"""
    return f"{PREFIX}task:timeout:{session_id}"


# ── 9. 当前生效模式 ──
MODE_ACTIVE_KEY = f"{PREFIX}mode:active"  # 永不过期，值 single_agent / multi_agent


def mode_gray_sessions_key() -> str:
    """csmp:mode:gray_sessions —— 灰度 session 列表。"""
    return f"{PREFIX}mode:gray_sessions"


def session_fallback_key(session_id: str) -> str:
    """csmp:session:{id}:fallback —— Redis 降级 checkpoint，TTL 72h。"""
    return f"{PREFIX}session:{session_id}:fallback"


def queue_pending_key() -> str:
    """csmp:queue:pending —— 工单接入有序排队。"""
    return f"{PREFIX}queue:pending"


# ── 10. 工单接入幂等锁（批次8） ──
def complaint_lock_key(complaint_id: str) -> str:
    """csmp:complaint:lock:{id} —— STRING(SET NX EX)，TTL 30min。"""
    return f"{PREFIX}complaint:lock:{complaint_id}"


# ── 11. 回调死信队列（批次8） ──
CALLBACK_DLQ_KEY = f"{PREFIX}callback:dlq"  # LIST，人工介入


def callback_dlq_key() -> str:
    """csmp:callback:dlq —— LIST。"""
    return CALLBACK_DLQ_KEY


# ── 12. 样本回流失败重试队列（批次8，零丢失） ──
BACKFLOW_RETRY_KEY = f"{PREFIX}backflow:retry"  # LIST，人工介入前不丢


def backflow_retry_key() -> str:
    """csmp:backflow:retry —— LIST，零丢失暂存。"""
    return BACKFLOW_RETRY_KEY

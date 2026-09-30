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
TTL_QUEUE_START = 600          # 10min —— 排队开始时间（排队 deadline）
TTL_EXEC_START = 1800          # 30min —— graph 执行开始时间（执行 deadline）
TTL_SESSION_FALLBACK = 259200  # 72h —— Redis 降级 checkpoint
TTL_MODE_ACTIVE: int | None = None    # 永不过期 —— 当前生效模式
# 注：批次3 全局永不过期键 LLM_DEGRADED_KEY/TTL_LLM_DEGRADED 已随
# complaint-resilience-wiring 废弃；LLM 降级标志改由 common 层按模型派生
# csmp:llm_degraded:{model}（SETEX TTL 60s，见 common.llm.reliability）。


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
# 已迁移至 common：csmp:llm_degraded:{model}（per-model，TTL 60s），
# 由 common.llm.reliability.invoke_llm / make_llm_breaker_key 派生，
# 本模块不再导出全局键 LLM_DEGRADED_KEY（complaint-resilience-wiring）。


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


# ── 9.5 G5：两级工单 deadline（互相独立，不删对方的 key）──
def queue_start_key(complaint_id: str) -> str:
    """csmp:complaint:queue_start:{id} —— 入队时间，TTL 10min（排队 deadline）。"""
    return f"{PREFIX}complaint:queue_start:{complaint_id}"


def exec_start_key(complaint_id: str) -> str:
    """csmp:complaint:exec_start:{id} —— graph 执行开始时间，TTL 30min（执行 deadline）。"""
    return f"{PREFIX}complaint:exec_start:{complaint_id}"


# ── 10. 工单接入幂等锁（批次8） ──
def complaint_lock_key(complaint_id: str) -> str:
    """csmp:complaint:lock:{id} —— STRING(SET NX EX)，TTL 30min。"""
    return f"{PREFIX}complaint:lock:{complaint_id}"


# ── 10b. 维5 研判幂等锁（complaint-resilience-wiring G4）──
def judge_lock_key(complaint_id: str) -> str:
    """csmp:complaint:judge_lock:{id} —— judge 接入 SETNX 锁，TTL 30min。

    与批次8 建单锁 complaint_lock_key 用途不同、key 不同，互不影响：
    建单锁保护 create_session；本锁保护 judge 接入（防同单并发重复研判）。
    """
    return f"{PREFIX}complaint:judge_lock:{complaint_id}"


# ── 10c. 跨进程图锁（批次11 / 多副本安全）──
# 与维5 judge_lock_key 的区别：judge_lock 是**接入幂等**（重复提交直接拒绝 B0005），
# 本组是**执行互斥**（防多副本对同一 thread_id 并发跑图导致 checkpoint 分叉）。
# TTL 由调用方按执行超时传入，不在此硬编。
def graph_lock_key(complaint_id: str) -> str:
    """csmp:complaint:graph_lock:{id} —— 跨进程图执行锁。"""
    return f"{PREFIX}complaint:graph_lock:{complaint_id}"


def resume_lock_key(complaint_id: str) -> str:
    """csmp:complaint:resume_lock:{id} —— 跨进程 resume 互斥。

    与 graph_lock 分开：judge 与 review-resume 可能并发发生（resume 侧本就要
    等 graph 跑完），共用一把锁会互相误判为"他人持有"。
    """
    return f"{PREFIX}complaint:resume_lock:{complaint_id}"


def maintenance_lock_key() -> str:
    """csmp:maintenance:lock —— 周期维护任务（超时转人工 + 三表裁剪）的跨副本互斥。

    多副本下每个实例都会跑维护循环，用此锁保证同一时刻只有一个副本执行，
    避免重复处理。维护任务幂等（UPDATE/DELETE 条件性），故锁 fail-open 无害：
    Redis 挂时各副本仍跑，重复执行只是空操作。
    """
    return f"{PREFIX}maintenance:lock"


# ── 11. 回调死信队列（批次8） ──
CALLBACK_DLQ_KEY = f"{PREFIX}callback:dlq"  # LIST，人工介入


def callback_dlq_key() -> str:
    """csmp:callback:dlq —— LIST。"""
    return CALLBACK_DLQ_KEY


# ── 12. 样本回流失败重试队列（批次8，零丢失） ──
# 投诉服务内部工作队列：生产方与消费方（backflow_service）都在 complaint-service 内，
# 故走本服务 REDIS_DB（common.resilience get_redis，配置驱动），
# 不是跨服务事件总线（etl.task.done / qc.check.completed 才固定 Redis 3 号库）。
BACKFLOW_RETRY_KEY = f"{PREFIX}backflow:retry"  # LIST，人工介入前不丢


def backflow_retry_key() -> str:
    """csmp:backflow:retry —— LIST，零丢失暂存（投诉服务内部队列，非 3 号库事件总线）。"""
    return BACKFLOW_RETRY_KEY

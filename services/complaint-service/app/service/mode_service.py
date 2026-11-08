"""模式服务（9.1）。

- single_agent / multi_agent 双模
- gray_percent (0~100) / gray_sessions
- 一致性达标（AI-人工≥85%、互一致≥80%）后全量切换
- 切换失败回滚 A5014
- T89：双签改为两阶段审批模型——判定来源 MUST 为已认证身份（JWT payload）
  **与** 独立审批记录（ApprovalLedger），MUST NOT 采信请求体 signers 自报值
  （JWT 角色词表 admin/supervisor/agent/qc_reviewer/ai_trainer 中不存在 ops/tech，
  旧 REQUIRED_SIGNERS 自报校验形同虚设，已随本整改移除）。
"""
from __future__ import annotations

import fnmatch
import json
import time
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Optional

import structlog

from app.events.publisher import publisher
from app.utils.redis_keys import MODE_ACTIVE_KEY, mode_gray_sessions_key
from common.errors.codes import ErrorCode

logger = structlog.get_logger()

KEY_ACTIVE = MODE_ACTIVE_KEY
KEY_GRAY = "csmp:mode:gray_percent"
KEY_SESSIONS = mode_gray_sessions_key()
VALID_MODES = {"single_agent", "multi_agent"}

AI_HUMAN_THRESHOLD = 0.85
CROSS_THRESHOLD = 0.80

# T89：审批记录键前缀（键含目标参数摘要）与显式 TTL（防无限期悬挂有效审批）
APPROVAL_KEY_PREFIX = "csmp:mode:approval:"
APPROVAL_TTL_SECONDS = 600


@dataclass(frozen=True)
class AuthenticatedApprover:
    """T89：审批身份值对象——只来自已认证 JWT payload（request.state.user）。"""

    sub: str
    role: str

    @classmethod
    def from_state_user(cls, payload: dict | None) -> "AuthenticatedApprover | None":
        if not payload:
            return None
        sub = payload.get("sub") or payload.get("user_id")
        role = payload.get("role")
        if not sub or not role:
            return None
        return cls(sub=str(sub), role=str(role))


@dataclass(frozen=True)
class ModeApprovalRecord:
    """T89：待审批记录——审批人 + 目标参数快照 + 登记时间 + TTL。"""

    approver: AuthenticatedApprover
    target_mode: str
    target_gray_percent: Optional[int]
    recorded_at: float
    ttl_seconds: int = APPROVAL_TTL_SECONDS

    def matches(self, mode: str, gray: Optional[int]) -> bool:
        """第二阶段参数必须与登记快照一致，防"A 审 A 模式、B 放 B 模式"拼接绕过。"""
        return self.target_mode == mode and self.target_gray_percent == gray

    def is_expired(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self.recorded_at >= self.ttl_seconds


class _InMemoryRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, ex=None):
        self._d[k] = str(v)
        return True

    def delete(self, k):
        return self._d.pop(k, None) is not None

    def keys(self, pattern: str = "*"):
        # T89：ApprovalLedger 按前缀扫描待审批记录；真实 Redis 原生支持，
        # 内存测试替身以 fnmatch 等价实现（过期判定由 Ledger 层兜底）
        return [k for k in self._d if fnmatch.fnmatch(k, pattern)]


def gray_bucket(session_id: str) -> int:
    """灰度分片 0–99：crc32 跨进程/重启稳定，不受 PYTHONHASHSEED 影响。

    原实现用内置 ``hash()``，字符串哈希随机化会让同一工单在不同进程落入
    不同分片，灰度比例与分流结果在重启后漂移。
    """
    return zlib.crc32(session_id.encode("utf-8")) % 100


class ApprovalLedger:
    """T89：模式切换独立审批记录（Redis 存储含 TTL）。

    - 单副本语义本批成立，多副本一致性由批 7（T112）装配承接；
    - Redis 客户端经 provider 惰性获取——沿用 ``mode_service._redis`` 装配路径，
      兼容 ``set_redis_client`` 动态替换，不触碰装配点；
    - 同一时刻至多保留一条待审批记录：异参数重新登记即拒绝旧记录。
    """

    def __init__(self, redis_provider: Callable[[], Any]) -> None:
        self._redis_provider = redis_provider

    @property
    def _redis(self):
        return self._redis_provider()

    @staticmethod
    def _record_key(target_mode: str, target_gray_percent: Optional[int]) -> str:
        digest = zlib.crc32(f"{target_mode}|{target_gray_percent}".encode("utf-8"))
        return f"{APPROVAL_KEY_PREFIX}{digest:08x}"

    @staticmethod
    def _decode(raw) -> ModeApprovalRecord | None:
        try:
            data = json.loads(raw)
            approver = AuthenticatedApprover(
                sub=str(data["approver_sub"]), role=str(data["approver_role"])
            )
            gray = data.get("target_gray_percent")
            return ModeApprovalRecord(
                approver=approver,
                target_mode=str(data["target_mode"]),
                target_gray_percent=int(gray) if gray is not None else None,
                recorded_at=float(data["recorded_at"]),
            )
        except Exception:  # noqa: BLE001 — 脏数据视为无记录，不阻断切换主流程
            return None

    def register(self, record: ModeApprovalRecord) -> None:
        """登记审批记录（同参数重复登记幂等覆盖；异参数旧记录清除=拒绝并重新登记）。"""
        self.clear()
        payload = json.dumps(
            {
                "approver_sub": record.approver.sub,
                "approver_role": record.approver.role,
                "target_mode": record.target_mode,
                "target_gray_percent": record.target_gray_percent,
                "recorded_at": record.recorded_at,
            }
        )
        self._redis.set(
            self._record_key(record.target_mode, record.target_gray_percent),
            payload,
            ex=record.ttl_seconds,
        )

    def find_pending(self) -> ModeApprovalRecord | None:
        for key in self._redis.keys(f"{APPROVAL_KEY_PREFIX}*"):
            raw = self._redis.get(key)
            if raw is None:
                continue
            record = self._decode(raw)
            if record is not None and not record.is_expired():
                return record
        return None

    @staticmethod
    def is_other_identity(
        record: ModeApprovalRecord, approver: AuthenticatedApprover
    ) -> bool:
        return record.approver.sub != approver.sub

    def clear(self) -> None:
        for key in self._redis.keys(f"{APPROVAL_KEY_PREFIX}*"):
            self._redis.delete(key)


class ModeService:
    def __init__(self, redis_client: Any = None,
                 consistency_provider: Any = None) -> None:
        self._redis = redis_client or _InMemoryRedis()
        self._consistency = consistency_provider
        # T89：审批记录组件，沿用 _redis 装配路径（provider 惰性取，兼容动态替换）
        self._ledger = ApprovalLedger(lambda: self._redis)

    def get_active(self) -> str:
        return self._redis.get(KEY_ACTIVE) or "single_agent"

    def get_gray_percent(self) -> int:
        v = self._redis.get(KEY_GRAY)
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def is_gray_session(self, session_id: str) -> bool:
        sessions = self._redis.get(KEY_SESSIONS)
        if not sessions:
            return False
        return session_id in sessions.split(",")

    def should_use_single_agent(self, session_id: str) -> bool:
        if self.get_active() == "single_agent":
            return True
        percent = self.get_gray_percent()
        if percent >= 100:
            return True
        if percent <= 0:
            return False
        return gray_bucket(session_id) < percent

    def check_consistency_ready(self) -> bool:
        if self._consistency is None:
            return False
        try:
            ai_human = self._consistency.get_ai_human_rate()
            cross = self._consistency.get_cross_rate()
            return ai_human >= AI_HUMAN_THRESHOLD and cross >= CROSS_THRESHOLD
        except Exception as exc:  # noqa: BLE001
            # T71：一致性判定失败按未就绪处理——降级语义值得暴露
            logger.warning(
                "consistency_check_failed_treat_not_ready",
                error=str(exc),
                exc_info=True,
            )
            return False

    def register_approval(
        self,
        approver: AuthenticatedApprover,
        target_mode: str,
        gray_percent: Optional[int],
    ) -> None:
        """T89：登记一条审批记录（同身份重复登记幂等覆盖、不推进）。"""
        self._ledger.register(
            ModeApprovalRecord(
                approver=approver,
                target_mode=target_mode,
                target_gray_percent=gray_percent,
                recorded_at=time.time(),
            )
        )

    def switch(self, mode: str, approver: AuthenticatedApprover | None,
               gray_percent: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """切换模式（T89 两阶段审批）。返回 (success, error_code)。

        - mode 词表与 gray 越界校验 A5014 先行（既有语义保持）；
        - approver 为 None（无已认证身份；请求体 signers 已被路由层忽略）→ A5014；
        - 存在另一不同身份且参数一致的待审批记录 → 推进既有写入/事件/回滚主体，
          成功后清空待审批记录（审批留痕语义由记录登记-推进-清空链路承载）；
        - 否则登记当前审批（第一阶段）返回 (True, None)——模式未变化，
          调用方以响应中的当前模式感知待审批状态。
        """
        if mode not in VALID_MODES:
            return False, ErrorCode.A5014
        if gray_percent is not None and not (0 <= gray_percent <= 100):
            return False, ErrorCode.A5014
        if approver is None:
            return False, ErrorCode.A5014

        try:
            pending = self._ledger.find_pending()
        except Exception:  # noqa: BLE001 — 审批记录设施故障按 A5014 拒绝，不向调用方抛出
            return False, ErrorCode.A5014
        if (
            pending is not None
            and self._ledger.is_other_identity(pending, approver)
            and pending.matches(mode, gray_percent)
        ):
            ok, err = self._advance(mode, gray_percent)
            if ok:
                try:
                    self._ledger.clear()
                except Exception:  # noqa: BLE001 — 切换已成功；残留记录由 TTL 兜底清理
                    logger.warning("mode_approval_clear_failed", exc_info=True)
            return ok, err
        try:
            self.register_approval(approver, mode, gray_percent)
        except Exception:  # noqa: BLE001 — 登记失败等同切换失败（A5014，无状态变更）
            return False, ErrorCode.A5014
        return True, None

    def _advance(self, mode: str, gray_percent: Optional[int]) -> tuple[bool, Optional[str]]:
        """既有切换/回滚主体（T89 零变更；gray 越界校验已前移至 switch）。"""
        # 读旧值也包进 try：Redis 异常 → A5014；脏 gray 由 get_gray_percent 兜底为 0
        old_mode = "single_agent"
        old_gray = 0
        try:
            old_mode = self.get_active()
            old_gray = self.get_gray_percent()
            self._redis.set(KEY_ACTIVE, mode)
            if gray_percent is not None:
                self._redis.set(KEY_GRAY, gray_percent)
            publisher.publish("complaint.mode.switched", {
                "trace_id": f"mode-{mode}",
                "from": old_mode, "to": mode,
            })
            return True, None
        except Exception:
            try:
                self._redis.set(KEY_ACTIVE, old_mode)
                self._redis.set(KEY_GRAY, old_gray)
            except Exception:
                # T40：切换失败后的回滚再失败 MUST NOT 静默丢弃——留 warning + 异常栈，
                # 此时 Redis 内模式状态可能不一致，需人工介入核对。
                logger.warning("mode_switch_rollback_failed", exc_info=True)
            return False, ErrorCode.A5014

    def full_switch_if_consistent(self, target_mode: str = "multi_agent") -> bool:
        if not self.check_consistency_ready():
            return False
        if target_mode not in VALID_MODES:
            return False
        # T89：服务内审批语义——登记服务内审批人记录后直接推进，
        # 不依赖 HTTP 两阶段跨请求留痕，审批记录留痕语义与 HTTP 路径一致
        self.register_approval(
            AuthenticatedApprover(sub="service-internal:consistency", role="admin"),
            target_mode,
            100,
        )
        ok, _ = self._advance(target_mode, 100)
        if ok:
            self._ledger.clear()
        return ok


mode_service = ModeService()

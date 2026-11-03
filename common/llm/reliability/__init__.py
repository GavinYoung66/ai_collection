"""LLM 调用可靠性包（T29 god module 拆分）：transport 之上的可靠性零件。

拆分后单一职责子模块（只搬移不改语义）：

- ``secrets.py``：占位密钥校验（fail-closed）
- ``service.py``：宿主服务名解析（per-key 熔断/限流/降级 key 前缀的单一事实源）
- ``retry.py``：有界指数退避重试原语（无业务语义）
- ``degrade.py``：Redis 服务前缀降级标志
- ``rate_limit.py``：LLM 模型维限流阈值（环境变量单一事实源）
- ``instrumentation.py``：llm_call 埋点（structlog 日志 + Prometheus 指标）
- ``orchestrator.py``：``invoke_llm`` 统一编排（选Key → 限流 → 熔断 → 有界重试
  → fallback → 降级）

兼容性约定：拆分前 ``common/llm/reliability.py`` 的全部导入面保持不变——
``from common.llm.reliability import X`` 与
``from common.llm import reliability as reliability_mod; reliability_mod.X``
均继续可用。对模块级可变状态的测试补丁点请指向所属子模块：
重试延迟/编排私有态 → ``orchestrator``，限流阈值 → ``rate_limit``。
"""
from common.llm.reliability.degrade import (
    RedisDegradeTracker,
    mark_degraded_flag,
)
from common.llm.reliability.instrumentation import _record_llm_call
from common.llm.reliability.orchestrator import (
    LLMCircuitOpenError,
    LLMRateLimitError,
    LLMTransport,
    _llm_breakers,
    invoke_llm,
    is_llm_orchestration_active,
    make_llm_breaker_key,
    reset_llm_transport,
    set_llm_transport,
)
from common.llm.reliability.rate_limit import (
    LLM_RATE_LIMIT_DEFAULT,
    LLM_RATE_WINDOW_DEFAULT,
    get_llm_rate_limit,
    load_llm_rate_limit,
)
from common.llm.reliability.retry import call_with_retry
from common.llm.reliability.secrets import (
    PLACEHOLDER_SECRETS,
    is_placeholder_secret,
    require_real_secret,
)
from common.llm.reliability.service import resolve_service_name, set_default_service

__all__ = [
    # 重试
    "call_with_retry",
    # 降级
    "RedisDegradeTracker",
    "mark_degraded_flag",
    # 密钥
    "PLACEHOLDER_SECRETS",
    "is_placeholder_secret",
    "require_real_secret",
    # 服务名
    "resolve_service_name",
    "set_default_service",
    # 限流配置
    "LLM_RATE_LIMIT_DEFAULT",
    "LLM_RATE_WINDOW_DEFAULT",
    "get_llm_rate_limit",
    "load_llm_rate_limit",
    # 编排
    "LLMCircuitOpenError",
    "LLMRateLimitError",
    "LLMTransport",
    "invoke_llm",
    "is_llm_orchestration_active",
    "make_llm_breaker_key",
    "set_llm_transport",
    "reset_llm_transport",
    # 兼容性私有面：qc job_store / 测试经包命名空间访问注册表；埋点函数供调试注入
    "_llm_breakers",
    "_record_llm_call",
]

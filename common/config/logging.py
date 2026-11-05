"""structlog 结构化 JSON 日志 + 脱敏过滤器。

脱敏统一复用 ``common.security.mask.mask_text``（T19）：手机号、身份证号、
银行卡号三类 PII 在同一实现内维护，禁止各服务另立正则。
"""
import logging

import structlog

from common.security.mask import mask_text


def _mask_processor(_, __, event_dict):
    """脱敏处理器：对日志中的字符串字段统一应用 PII 掩码（含嵌套 dict/list）。"""
    for key, value in event_dict.items():
        event_dict[key] = mask_nested(value)
    return event_dict


# T49：递归脱敏深度上限——超过该嵌套深度的容器原样返回（防深嵌套攻击面）。
_MASK_DEPTH_LIMIT = 8

# W2：循环引用截断占位符——命中重入时返回该字符串，绝不携带明文子树
# （原实现返回原对象，自引用子树内的 PII 未经掩码即进入日志渲染）。
_CIRCULAR_PLACEHOLDER = "[circular]"


def _mask_str(text: str) -> str:
    """字符串叶子掩码 best-effort：mask_text 异常时返回原值，不阻断日志输出。"""
    try:
        return mask_text(text)
    except Exception:
        return text


def mask_nested(value, depth: int = 0, _seen: set[int] | None = None):
    """递归脱敏：dict 逐 key+value / list·tuple·set 逐元素下钻，字符串叶子应用 mask_text。

    T49 单一实现：structlog ``_mask_processor`` 与 call_log_sink 落盘统一经此
    覆盖（mcp-gateway 审计 entry 的嵌套 params 自动受益）。

    - 深度上限 8：超限原样返回（防深嵌套结构放大开销/攻击面）；
    - 循环引用：访问中容器 id 记入祖先集合，重入截断为 ``[circular]`` 占位
      （W2：不再返回原对象，避免自引用子树明文泄漏，且保证渲染可 JSON 化）；
    - W2 下钻面补齐：bytes/bytearray 解码后按 str 掩码；set/frozenset 构建
      新集合掩码；dict 的 str key 同样过掩码（key 亦可能携带 PII）；
    - 字符串叶子脱敏失败 best-effort 返回原值，不阻断日志输出（mask_text 为
      纯正则实现，此分支为防御性兜底）；
    - 其余类型原样返回。
    """
    if _seen is None:
        _seen = set()
    if depth > _MASK_DEPTH_LIMIT:
        return value
    if isinstance(value, str):
        return _mask_str(value)
    if isinstance(value, (bytes, bytearray)):
        # W2：bytes 叶子 best-effort 解码（非法 UTF-8 以 replace 容错）后掩码
        try:
            return _mask_str(bytes(value).decode("utf-8", "replace"))
        except Exception:
            return value
    if isinstance(value, dict):
        vid = id(value)
        if vid in _seen:
            return _CIRCULAR_PLACEHOLDER
        _seen.add(vid)
        try:
            return {
                (_mask_str(k) if isinstance(k, str) else k):
                    mask_nested(v, depth + 1, _seen)
                for k, v in value.items()
            }
        finally:
            _seen.discard(vid)
    if isinstance(value, (list, tuple)):
        vid = id(value)
        if vid in _seen:
            return _CIRCULAR_PLACEHOLDER
        _seen.add(vid)
        try:
            masked = [mask_nested(v, depth + 1, _seen) for v in value]
            return tuple(masked) if isinstance(value, tuple) else masked
        finally:
            _seen.discard(vid)
    if isinstance(value, (set, frozenset)):
        # W2：set 不可原地掩码（元素不可变），构建新集合下钻
        vid = id(value)
        if vid in _seen:
            return _CIRCULAR_PLACEHOLDER
        _seen.add(vid)
        try:
            masked = {mask_nested(v, depth + 1, _seen) for v in value}
            return frozenset(masked) if isinstance(value, frozenset) else masked
        finally:
            _seen.discard(vid)
    return value


def setup_logging(env: str = "dev", level: str | None = None) -> None:
    """初始化 structlog。

    - ``level``：显式日志级别名（如 DEBUG/INFO/WARN/ERROR），优先于 env 推断。
    - 未传 ``level`` 时：``env=="dev"`` → DEBUG，否则 INFO。
    """
    if level:
        log_level = getattr(logging, str(level).upper(), None)
        if not isinstance(log_level, int):
            # WARN 在 stdlib 为 WARNING
            log_level = logging.WARNING if str(level).upper() == "WARN" else logging.INFO
    else:
        log_level = logging.DEBUG if env == "dev" else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

"""EventBus factory — ``EVENT_BUS`` env selects adapter singleton."""
from __future__ import annotations

import os

from common.eventbus.base import EventBus
from common.eventbus.rocketmq import RocketMQEventBus

_VALID = frozenset({"rocketmq"})
_instance: EventBus | None = None
_instance_mode: str | None = None


def get_event_bus(*, force_new: bool = False) -> EventBus:
    """Return process-wide EventBus singleton for ``EVENT_BUS``.

    Raises:
        ValueError: when ``EVENT_BUS`` is missing/illegal (only ``rocketmq``).
    """
    global _instance, _instance_mode
    mode = (os.getenv("EVENT_BUS") or "").strip().lower()
    if mode not in _VALID:
        raise ValueError(
            f"EVENT_BUS must be one of {sorted(_VALID)}, got {mode!r}"
        )
    if (
        not force_new
        and _instance is not None
        and _instance_mode == mode
    ):
        return _instance
    _instance = _build(mode)
    _instance_mode = mode
    return _instance


def reset_event_bus() -> None:
    """Clear singleton (tests)."""
    global _instance, _instance_mode
    _instance = None
    _instance_mode = None


def _build(mode: str) -> EventBus:
    if mode == "rocketmq":
        return RocketMQEventBus()
    raise ValueError(f"EVENT_BUS must be one of {sorted(_VALID)}, got {mode!r}")

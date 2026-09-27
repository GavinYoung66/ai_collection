"""Unified event bus: envelope, Protocol, and transport adapters."""
from __future__ import annotations

from common.eventbus.base import EventBus, EventHandler
from common.eventbus.envelope import (
    DATA_WHITELIST,
    EventEnvelope,
    build_envelope,
    parse_envelope,
    peek_reject_meta,
)
from common.eventbus.factory import get_event_bus, reset_event_bus
from common.eventbus.util import whitelist_data

__all__ = [
    "DATA_WHITELIST",
    "EventBus",
    "EventEnvelope",
    "EventHandler",
    "build_envelope",
    "parse_envelope",
    "peek_reject_meta",
    "get_event_bus",
    "reset_event_bus",
    "whitelist_data",
]

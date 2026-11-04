"""Unified event bus: envelope, Protocol, and transport adapters."""
from __future__ import annotations

from common.eventbus.base import EventBus, EventHandler
from common.eventbus.envelope import (
    EventEnvelope,
    allowed_event_types,
    build_envelope,
    data_whitelist,
    parse_envelope,
    peek_reject_meta,
)
from common.eventbus.factory import get_event_bus, reset_event_bus
from common.eventbus.util import whitelist_data

__all__ = [
    "EventBus",
    "EventEnvelope",
    "EventHandler",
    "allowed_event_types",
    "data_whitelist",
    "build_envelope",
    "parse_envelope",
    "peek_reject_meta",
    "get_event_bus",
    "reset_event_bus",
    "whitelist_data",
]

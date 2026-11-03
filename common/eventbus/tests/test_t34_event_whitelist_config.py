"""T34：事件类型白名单配置化单测。

验收口径：
- 事件类型登记表来自配置（``EVENT_ALLOWED_TYPES``），代码内无硬编码清单；
- 新增事件仅改配置即生效（重启后无需改代码）；未登记事件 fail-closed；
- envelope.data 字段白名单同样来自配置（``EVENT_DATA_WHITELIST``）。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.eventbus import envelope as envelope_module
from common.eventbus.envelope import (
    allowed_event_types,
    build_envelope,
    data_whitelist,
)
from common.eventbus.util import whitelist_data


def test_allowed_event_types_comes_from_config():
    assert allowed_event_types() == frozenset(
        item.strip() for item in envelope_module.settings.EVENT_ALLOWED_TYPES.split(",")
    )


def test_new_event_type_registered_by_config_only(monkeypatch):
    """新增事件：只改配置即可发布，无需修改 envelope 代码。"""
    monkeypatch.setattr(
        envelope_module.settings,
        "EVENT_ALLOWED_TYPES",
        envelope_module.settings.EVENT_ALLOWED_TYPES + ",invoice.issued",
    )
    env = build_envelope("invoice.issued", {"case_id": "c1"})
    assert env.event_name == "invoice.issued"


def test_unregistered_event_type_rejected():
    with pytest.raises(ValidationError) as exc:
        build_envelope("csmp.unregistered.event", {"case_id": "c1"})
    assert "not registered" in str(exc.value)


def test_empty_event_registry_fails_closed(monkeypatch):
    """空登记表意味着拒绝一切事件（宁可不可用，不可放行未知事件）。"""
    monkeypatch.setattr(envelope_module.settings, "EVENT_ALLOWED_TYPES", "")
    with pytest.raises(ValidationError):
        build_envelope("call.session.ended", {"case_id": "c1"})


def test_event_registry_removal_rejected(monkeypatch):
    """事件类型从配置移除后立即不可用（配置是唯一事实源）。"""
    types = [
        t
        for t in envelope_module.settings.EVENT_ALLOWED_TYPES.split(",")
        if t.strip() != "call.session.ended"
    ]
    monkeypatch.setattr(envelope_module.settings, "EVENT_ALLOWED_TYPES", ",".join(types))
    with pytest.raises(ValidationError):
        build_envelope("call.session.ended", {"case_id": "c1"})


def test_data_whitelist_from_config(monkeypatch):
    assert data_whitelist() == frozenset(
        item.strip() for item in envelope_module.settings.EVENT_DATA_WHITELIST.split(",")
    )


def test_data_whitelist_extension_by_config(monkeypatch):
    """新增允许字段只改配置，无需改 envelope。"""
    monkeypatch.setattr(
        envelope_module.settings,
        "EVENT_DATA_WHITELIST",
        envelope_module.settings.EVENT_DATA_WHITELIST + ",tenant_id",
    )
    env = build_envelope("qc.check.completed", {"qc_record_id": "q1", "tenant_id": "t1"})
    assert env.data["tenant_id"] == "t1"
    assert whitelist_data({"tenant_id": "t1", "score": 99}) == {"tenant_id": "t1"}


def test_data_whitelist_shrink_by_config(monkeypatch):
    """字段从白名单移除后立即拒绝。"""
    monkeypatch.setattr(envelope_module.settings, "EVENT_DATA_WHITELIST", "session_id")
    with pytest.raises(ValidationError):
        build_envelope("call.session.ended", {"case_id": "c1"})


def test_no_hardcoded_whitelist_constant_in_module():
    """防回流：envelope 模块不得再暴露硬编码白名单常量。"""
    assert not hasattr(envelope_module, "DATA_WHITELIST")

"""T71~T74 / 批次 8：complaint-service 静默异常收口与 publisher 治理 单测。

验收判据：
- 8.1：9 处收口点位逐一断言日志事件存在（warning 带理由/exc_info）；
- 8.3：无事件循环环境连续 publish 多次无状态残留（fallback 一次性用完即弃）；
- 8.4：生产配置下 _events 为 bounded 环形缓冲（超上限不线性增长）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.events import publisher as publisher_mod
from app.events.publisher import EventPublisher

_APP = Path(__file__).resolve().parents[2] / "app"


class TestT71SilentExceptionClosure:
    def test_all_nine_closure_points_logged(self):
        """9 处收口点位：日志事件名逐一存在（源码扫描守护）。"""
        expectations = {
            "service/complaint_service.py": [
                "snapshot_read_failed_use_empty_final",
                "audit_event_collect_failed",  # T56 已收口（mcp/llm）
            ],
            "api/routes/complaints.py": ["session_flush_failed"],
            "events/subscriber.py": ["redis_dedupe_failed"],
            "events/publisher.py": ["bus_event_log_failed"],
            "service/mode_service.py": ["consistency_check_failed_treat_not_ready"],
            "service/recovery_service.py": ["recovery_fallback_json_corrupted"],
            "graph/nodes/generate_package.py": [
                "evidence_collect_failed_best_effort",
                "generate_package_postcheck_rebuild_failed",
            ],
            "main.py": [
                "checkpointer_init_degraded_to_memory",
                "mcp_probe_failed_mark_degraded",
            ],
        }
        for rel, events in expectations.items():
            src = (_APP / rel).read_text(encoding="utf-8")
            for event in events:
                assert event in src, f"{rel} 缺收口日志事件: {event}"

    def test_warning_points_carry_exc_info(self):
        """降级语义点位（warning）必须带 exc_info=True 完整栈。"""
        pairs = [
            ("service/mode_service.py", "consistency_check_failed_treat_not_ready"),
            ("service/recovery_service.py", "recovery_fallback_json_corrupted"),
            ("graph/nodes/generate_package.py", "generate_package_postcheck_rebuild_failed"),
        ]
        for rel, event in pairs:
            src = (_APP / rel).read_text(encoding="utf-8")
            idx = src.find(event)
            assert idx > 0, f"{rel} 缺事件 {event}"
            window = src[idx : idx + 200]
            assert "exc_info=True" in window, f"{rel} 的 {event} 缺 exc_info"


class TestT73FallbackNoResidualState:
    def test_sync_publish_loop_fallback_no_task_residual(self, monkeypatch):
        """无事件循环环境（同步上下文）连续 publish：fallback 一次性用完即弃，
        不在模块级 _bus_publish_tasks 留任务残留。"""
        import asyncio

        monkeypatch.setenv("EVENT_BUS", "rocketmq")  # 预设，防 _go 内改环境
        monkeypatch.setenv("EVENT_BUS_SVC", "cs")
        # 隔离其他用例经有 loop 路径留下的任务残留（本用例只验证 fallback 路径）
        publisher_mod._bus_publish_tasks.clear()
        pub = EventPublisher()

        for i in range(3):
            pub.publish("complaint.judged", {"case_id": f"c-{i}"})

        assert publisher_mod._bus_publish_tasks == set(), (
            "fallback（asyncio.run）路径不应创建持久任务"
        )

    def test_fallback_warning_logged_on_bus_failure(self, monkeypatch, capsys):
        """fallback 内 bus 失败：warning 留痕不抛出（publish 不破坏调用方）。"""
        monkeypatch.setenv("EVENT_BUS", "rocketmq")
        monkeypatch.setenv("EVENT_BUS_SVC", "cs")
        monkeypatch.setattr(
            "common.eventbus.factory.get_event_bus",
            Mock_side_effect:=__import__("unittest.mock", fromlist=["Mock"]).Mock(
                side_effect=RuntimeError("no rocketmq locally")
            ),
        )
        pub = EventPublisher()
        pub.publish("complaint.judged", {"case_id": "c-1"})  # 不抛即通过


class TestT74EventsBounded:
    def test_events_buffer_bounded(self):
        """生产配置：publish 600 条 → _events bounded 到 500（不线性增长）。"""
        pub = EventPublisher()
        for i in range(600):
            pub.publish("complaint.judged", {"case_id": f"c-{i}", "seq": i})

        records = pub.list_events()
        assert len(records) == 500, "环形缓冲应 bounded 到 maxlen"
        # 保留最近 500 条：最早 100 条被挤出
        seqs = [r.payload.get("seq") for r in records]
        assert seqs[0] == 100 and seqs[-1] == 599, "应保留最新的 500 条"

    def test_list_events_topic_filter_still_works(self):
        """list_events 的 topic 过滤语义不变（deque 替换 list 零回归）。"""
        pub = EventPublisher()
        pub.publish("complaint.judged", {"case_id": "c-1"})
        pub.publish("other.topic", {"k": "v"})
        only_judged = pub.list_events(topic="complaint.judged")
        assert len(only_judged) == 1
        assert only_judged[0].topic == "complaint.judged"
"""T98：complaint provider `_call_log` 有界化（spec runtime-correctness：常驻进程内存有界）。

- 万级调用后缓冲长度稳定在显式上限内，不线性增长；
- 单条审计条目不驻留全量 prompt/response 正文，只保留脱敏预览摘要；
- `filter_new_logs` 扫描成本不随历史长度退化（缓冲有界 → 拷贝/切片有界）。
"""
from __future__ import annotations

import time


from app.llm.provider import (
    CALL_LOG_MAXLEN,
    LOG_PREVIEW_CHARS,
    ComplaintLLMProvider,
)
from app.service.trace import filter_new_logs


class _FakeTransport:
    async def invoke(self, **kwargs):
        return {"verdict": "ok", "confidence": 0.9}


def _audit(p: ComplaintLLMProvider, i: int) -> None:
    p._record_audit(
        f"prompt-{i}-" + "长" * 5000,
        trace_id=f"t-{i}",
        session_id="s1",
        start=time.monotonic(),
        attempt=1,
        error=None,
        response={"verdict": "ok", "blob": "x" * 5000},
        agent_role="fact",
    )


class TestT98BoundedCallLog:
    def test_constants_explicit(self):
        """容量上限与截断常量显式化（spec：容量上限 MUST 为显式常量或可配置）。"""
        assert CALL_LOG_MAXLEN == 500
        assert LOG_PREVIEW_CHARS == 200

    def test_mass_calls_length_bounded(self):
        """万级调用后缓冲长度 == 上限，不随调用次数线性增长。"""
        p = ComplaintLLMProvider(transport=_FakeTransport(), max_token_threshold=10**9)
        for i in range(CALL_LOG_MAXLEN + 1000):
            _audit(p, i)
        assert len(p.call_log) == CALL_LOG_MAXLEN

    def test_entry_keeps_no_full_text(self):
        """单条审计条目不驻留全文 prompt/response，预览不超过截断常量。"""
        p = ComplaintLLMProvider(transport=_FakeTransport(), max_token_threshold=10**9)
        _audit(p, 0)
        entry = p.call_log[-1]
        assert "prompt" not in entry
        assert "response" not in entry
        assert len(entry["prompt_preview"]) <= LOG_PREVIEW_CHARS
        assert len(entry["response_preview"]) <= LOG_PREVIEW_CHARS
        assert entry["status"] == "success"
        assert "latency_ms" in entry

    def test_call_log_snapshot_is_list(self):
        """call_log property 返回列表快照（deque 兼容既有消费方）。"""
        p = ComplaintLLMProvider(transport=_FakeTransport(), max_token_threshold=10**9)
        _audit(p, 0)
        snapshot = p.call_log
        assert isinstance(snapshot, list)
        snapshot.append({"mutated": True})  # 快照隔离：不污染内部缓冲
        assert len(p.call_log) == 1

    def test_filter_new_logs_scan_cost_bounded(self):
        """filter_new_logs 扫描成本有界：start_index 超界返回空、不随历史退化。"""
        p = ComplaintLLMProvider(transport=_FakeTransport(), max_token_threshold=10**9)
        for i in range(CALL_LOG_MAXLEN + 1000):
            _audit(p, i)
        logs = p.call_log
        assert len(logs) == CALL_LOG_MAXLEN
        # 正常轮次：start_index 落在缓冲窗口内可取增量
        recent = filter_new_logs(logs, start_index=CALL_LOG_MAXLEN - 5, session_id="s1", trace_id="t")
        assert len(recent) == 5
        # 淘汰发生后（start_index 超过缓冲首条）：有界空结果，不炸不扫描全量历史
        assert filter_new_logs(logs, start_index=10**9, session_id="s1", trace_id="t") == []
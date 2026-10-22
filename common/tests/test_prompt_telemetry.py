"""prompt 版本 metadata 单测（Langfuse 未启用时仍需可断言）。"""
from __future__ import annotations

from common.prompt.telemetry import prompt_metadata, resolve_prompt_metadata
from common.trace.langfuse_gate import LangfuseRun


def test_prompt_metadata_keys() -> None:
    meta = prompt_metadata("rag_answer", "1.0.0")
    assert meta == {"prompt_id": "rag_answer", "prompt_version": "1.0.0"}


def test_resolve_metadata_from_template() -> None:
    assert resolve_prompt_metadata("rag_answer", "rag-service") == {
        "prompt_id": "rag_answer",
        "prompt_version": "1.0.0",
    }
    assert resolve_prompt_metadata("qc_review", "qc-service") == {
        "prompt_id": "qc_review",
        "prompt_version": "1.0.0",
    }


def test_metadata_survives_langfuse_disabled(monkeypatch) -> None:
    """未启用 Langfuse 时写入 metadata 不得抛错，且退化为空操作。"""
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    run = LangfuseRun(
        "prompt_test",
        {"case": "disabled"},
        metadata=prompt_metadata("rag_answer", "1.0.0"),
    )
    assert run._span is None
    run.end(outputs={"ok": True})

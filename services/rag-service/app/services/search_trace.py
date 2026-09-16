"""检索链路 Trace 收集 + LangSmith / Langfuse 可选上报（与 kb_search_logs 并存）。"""
import time
from contextlib import contextmanager
from typing import Any

import structlog

from app.settings import settings
from common.trace.langfuse_gate import (
    LangfuseRun,
    configure_langfuse_env as _configure_langfuse_env,
    langfuse_enabled,
)
from common.trace.langsmith_gate import (
    configure_langsmith_env as _configure_langsmith_env,
    langsmith_enabled,
    langsmith_project,
)

logger = structlog.get_logger()


def configure_langsmith_env() -> None:
    """启动时按统一开关写入 / 清除 LangSmith SDK 环境变量（转发 common 闸门）。"""
    _configure_langsmith_env(settings)


def configure_langfuse_env() -> None:
    """启动时按统一开关写入 / 清除 Langfuse SDK 环境变量（转发 common 闸门）。"""
    _configure_langfuse_env(settings)


class SearchTraceCollector:
    """单次 hybrid_search 的步骤耗时收集；可选同步 LangSmith / Langfuse Run 树。"""

    def __init__(self, trace_id: str, query: str):
        self.trace_id = trace_id
        self.query = query
        self._start = time.perf_counter()
        self._steps: list[dict[str, Any]] = []
        self._root_run = None
        self._init_langsmith_root()
        self._langfuse = LangfuseRun(
            "rag_hybrid_search",
            {"query": query, "trace_id": trace_id},
            settings=settings,
            metadata={"trace_id": trace_id},
        )

    def _init_langsmith_root(self) -> None:
        if not langsmith_enabled(settings):
            return
        try:
            configure_langsmith_env()
            from langsmith.run_trees import RunTree

            self._root_run = RunTree(
                name="rag_hybrid_search",
                run_type="chain",
                inputs={"query": self.query, "trace_id": self.trace_id},
                project_name=langsmith_project(settings, "rag-service"),
            )
            self._root_run.post()
        except Exception as e:
            logger.warning("langsmith_root_init_failed", error=str(e))
            self._root_run = None

    @contextmanager
    def step(self, name: str, **meta: Any):
        t0 = time.perf_counter()
        child = None
        if self._root_run is not None:
            try:
                child = self._root_run.create_child(
                    name=name,
                    run_type="tool",
                    inputs={k: v for k, v in meta.items() if v is not None},
                )
                child.post()
            except Exception as e:
                logger.warning("langsmith_child_init_failed", step=name, error=str(e))
        lf_child = self._langfuse.create_child(
            name, {k: v for k, v in meta.items() if v is not None}
        )
        try:
            yield
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
            record: dict[str, Any] = {"name": name, "latency_ms": ms}
            record.update({k: v for k, v in meta.items() if v is not None})
            self._steps.append(record)
            if child is not None:
                try:
                    child.end(outputs={"latency_ms": ms})
                except Exception as e:
                    logger.warning("langsmith_child_end_failed", step=name, error=str(e))
            if lf_child is not None:
                lf_child.end(outputs={"latency_ms": ms})

    def finish(self, outputs: dict[str, Any] | None = None) -> dict:
        total_ms = int((time.perf_counter() - self._start) * 1000)
        summary = outputs or {}
        if self._root_run is not None:
            try:
                self._root_run.end(outputs={"total_ms": total_ms, **summary})
            except Exception as e:
                logger.warning("langsmith_root_end_failed", error=str(e))
        self._langfuse.end(outputs={"total_ms": total_ms, **summary})
        return {
            "trace_id": self.trace_id,
            "total_ms": total_ms,
            "steps": self._steps,
            "summary": summary,
            "langsmith_enabled": langsmith_enabled(settings),
            "langfuse_enabled": langfuse_enabled(settings),
        }

"""检索链路 Trace 收集 + LangSmith 可选上报（与 kb_search_logs 并存）。"""
import os
import time
from contextlib import contextmanager
from typing import Any

import structlog

from app.settings import settings

logger = structlog.get_logger()

_LANGSMITH_ENV_KEYS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)


def configure_langsmith_env() -> None:
    """启动时将 LangSmith 配置写入进程环境（供 RunTree / SDK 读取）。"""
    if not settings.LANGSMITH_ENABLED or not settings.LANGSMITH_API_KEY:
        for key in _LANGSMITH_ENV_KEYS:
            os.environ.pop(key, None)
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
    os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
    if settings.LANGSMITH_ENDPOINT:
        os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT


class SearchTraceCollector:
    """单次 hybrid_search 的步骤耗时收集；可选同步 LangSmith Run 树。"""

    def __init__(self, trace_id: str, query: str):
        self.trace_id = trace_id
        self.query = query
        self._start = time.perf_counter()
        self._steps: list[dict[str, Any]] = []
        self._root_run = None
        self._init_langsmith_root()

    def _init_langsmith_root(self) -> None:
        if not (settings.LANGSMITH_ENABLED and settings.LANGSMITH_API_KEY):
            return
        try:
            from langsmith.run_trees import RunTree

            self._root_run = RunTree(
                name="rag_hybrid_search",
                run_type="chain",
                inputs={"query": self.query, "trace_id": self.trace_id},
                project_name=settings.LANGSMITH_PROJECT,
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

    def finish(self, outputs: dict[str, Any] | None = None) -> dict:
        total_ms = int((time.perf_counter() - self._start) * 1000)
        summary = outputs or {}
        if self._root_run is not None:
            try:
                self._root_run.end(outputs={"total_ms": total_ms, **summary})
            except Exception as e:
                logger.warning("langsmith_root_end_failed", error=str(e))
        return {
            "trace_id": self.trace_id,
            "total_ms": total_ms,
            "steps": self._steps,
            "summary": summary,
            "langsmith_enabled": bool(
                settings.LANGSMITH_ENABLED and settings.LANGSMITH_API_KEY
            ),
        }

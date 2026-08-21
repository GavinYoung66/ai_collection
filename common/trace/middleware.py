"""X-Trace-Id 全链路追踪，基于 contextvars。"""
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id)


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        set_trace_id(trace_id)

        response: Response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

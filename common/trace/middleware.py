"""X-Trace-Id 全链路追踪，基于 contextvars。

T99：纯 ASGI 形态——MUST NOT 继承 BaseHTTPMiddleware（其 call_next 会缓冲
响应体，破坏 /mcp 的 SSE 逐块输出；与 mcp-gateway auth.py/rate_limit.py 的
"不用 BaseHTTPMiddleware，避免缓冲破坏 /mcp 的 SSE 流" SHALL NOT 声明对齐），
MUST NOT 读取或缓冲 http.response.body。
"""
import uuid
from contextvars import ContextVar

_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")

_TRACE_HEADER_RAW = b"x-trace-id"


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id)


class TraceMiddleware:
    """纯 ASGI 追踪中间件（T99）。

    - ``scope.type != "http"``（lifespan/websocket）原样透传；
    - 入站 ``X-Trace-Id`` 透传、缺省生成 UUID4，写入 contextvar（语义与
      BaseHTTPMiddleware 版本一致）；
    - send 包装器仅拦截 ``http.response.start``：追加（上游已带同名头则替换）
      ``X-Trace-Id`` 响应头，保证响应头与 contextvar 一致；其余全部 ASGI
      消息逐条原样转发（SSE 多块 body 无聚合、不读取响应体）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers") or []:
            if name.lower() == _TRACE_HEADER_RAW:
                incoming = value.decode("latin-1")
                break
        trace_id = incoming or str(uuid.uuid4())
        set_trace_id(trace_id)

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                mutated: list[tuple[bytes, bytes]] = []
                replaced = False
                for name, value in message.get("headers") or []:
                    if name.lower() == _TRACE_HEADER_RAW:
                        # 上游已带同名头：首个替换为 contextvar 值、其余丢弃
                        if not replaced:
                            mutated.append((_TRACE_HEADER_RAW, trace_id.encode("latin-1")))
                            replaced = True
                        continue
                    mutated.append((name, value))
                if not replaced:
                    mutated.append((_TRACE_HEADER_RAW, trace_id.encode("latin-1")))
                message = dict(message)
                message["headers"] = mutated
            await send(message)

        await self.app(scope, receive, send_wrapper)

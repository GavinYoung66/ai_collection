"""ASGI 中间件共享小工具：读请求 body + 单次重放。

auth / rate_limit 两个中间件都需先读 body 做 JSON-RPC 解析、再放行给下游
（放行路径只重放一次 buffered body），此处抽公共实现避免两处重复。
"""
from __future__ import annotations

from typing import Any


async def read_body(receive) -> bytes:
    """读取完整 http.request body（含 more_body 分片），非 request 消息跳过。"""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            break
    return b"".join(chunks)


def make_replay_receive(receive, body: bytes):
    """返回「单次重放 buffered body，此后回退原 receive」的 receive 工厂。

    streamable-http 长连接 SSE 会持续调 receive 轮询 http.disconnect，若无限重放
    http.request 会导致空转挂起，故 buffered body 只重放一次。
    """
    sent = False

    async def replay() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay

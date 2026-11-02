"""共享 LLM HTTP 通道。

- OpenAI 兼容：`post_chat_completions`
- 通用 JSON POST（DashScope 原生等）：`post_json`

职责边界（对齐强制8）：
- 本模块：发请求、超时、HTTP 错误包装
- 上层 Provider：Key 解析、重试、熔断/降级、业务编排
"""
from __future__ import annotations

import asyncio
import contextvars
import weakref
from typing import Any, Optional

import httpx

# 最近一次 ``post_chat_completions`` 成功响应中的 ``usage``（供编排层 token 埋点透传）。
# 口径：成功拿到 dict body 时写入 ``body.get("usage")``（无 usage 字段即 None）；
# HTTP 非 200 / 异常路径不写，保持上一值由调用方 reset 兜底。
# 重要：调用方若经 ``asyncio.wait_for`` 包裹本函数，写入只落在子 Task 的上下文副本里，
# 必须在**同一个 Task 内**读取（见 reliability._call_model 的内层协程）。
_llm_last_usage: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "llm_last_usage", default=None
)

# 共享 HTTP 客户端（T16）：同一事件循环内复用同一个 AsyncClient，保留 keep-alive
# 连接池，避免每次调用新建 client。按事件循环隔离——测试/重载切换 loop 时新建，
# 防止跨 loop 复用已绑定的连接池。
_shared_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, httpx.AsyncClient
] = weakref.WeakKeyDictionary()


def _get_shared_client() -> httpx.AsyncClient:
    """取得当前事件循环的共享 client；已关闭则重建。"""
    loop = asyncio.get_running_loop()
    client = _shared_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient()
        _shared_clients[loop] = client
    return client


async def aclose_shared_client() -> None:
    """显式关闭当前事件循环的共享 client（服务 shutdown 钩子调用，幂等）。"""
    client = _shared_clients.pop(asyncio.get_running_loop(), None)
    if client is not None and not client.is_closed:
        await client.aclose()


async def post_json(
    *,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float = 120.0,
    client: httpx.AsyncClient | None = None,
    error_label: str = "LLM",
) -> dict[str, Any]:
    """Bearer JSON POST；成功返回响应 JSON；HTTP 错误抛 RuntimeError。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _do(c: httpx.AsyncClient) -> dict[str, Any]:
        resp = await c.post(url, headers=headers, json=payload, timeout=timeout)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (e.response.text or "")[:300]
            raise RuntimeError(
                f"{error_label} HTTP {e.response.status_code}: {body}"
            ) from e
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"{error_label} response is not a JSON object")
        return data

    if client is not None:
        return await _do(client)
    return await _do(_get_shared_client())


async def post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    timeout: float = 120.0,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    seed: Optional[int] = None,
    response_format: Optional[dict[str, Any]] = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST `{base_url}/chat/completions`，成功返回 JSON body；HTTP 错误抛 RuntimeError。"""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if top_p is not None:
        payload["top_p"] = top_p
    if seed is not None:
        payload["seed"] = seed
    if response_format is not None:
        payload["response_format"] = response_format

    body = await post_json(
        url=url,
        api_key=api_key,
        payload=payload,
        timeout=timeout,
        client=client,
        error_label=f"LLM model={model}",
    )
    # 成功返回 dict body：透传 usage（无 usage 字段时为 None，交由编排层 reset 兜底）。
    _llm_last_usage.set(body.get("usage"))
    return body


def message_content_from_completions(body: dict[str, Any]) -> str:
    """从 chat/completions 响应取出 message.content（含空 content 时尝试 reasoning_content）。"""
    try:
        msg = body["choices"][0]["message"]
        raw = (msg.get("content") or "").strip()
        if not raw:
            raw = (msg.get("reasoning_content") or "").strip()
        return raw
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""

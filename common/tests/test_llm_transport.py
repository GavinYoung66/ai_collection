"""common.llm.transport 基础单测。"""
import httpx
import pytest

from common.llm.transport import message_content_from_completions, post_json


def test_message_content_from_completions_basic():
    body = {"choices": [{"message": {"content": "  hello  "}}]}
    assert message_content_from_completions(body) == "hello"


def test_message_content_from_completions_reasoning_fallback():
    body = {"choices": [{"message": {"content": "", "reasoning_content": "{}"}}]}
    assert message_content_from_completions(body) == "{}"


def test_message_content_from_completions_bad_shape():
    assert message_content_from_completions({}) == ""


@pytest.mark.asyncio
async def test_post_json_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        return httpx.Response(200, json={"output": {"text": "ok"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        data = await post_json(
            url="https://example.com/v1/run",
            api_key="sk-test",
            payload={"model": "m"},
            client=client,
            error_label="DashScope",
        )
    assert data["output"]["text"] == "ok"


@pytest.mark.asyncio
async def test_post_json_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError, match="DashScope HTTP 500"):
            await post_json(
                url="https://example.com/v1/run",
                api_key="sk-test",
                payload={},
                client=client,
                error_label="DashScope",
            )

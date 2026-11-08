"""T133：dp 出站 client 长生命周期复用（qc / complaint）。

spec delta「出站客户端复用」验收：
- 出站调用（含重试循环内部）复用同一 AsyncClient，MUST NOT 每请求新建
- 服务关停时出站 client 显式释放（无悬挂连接）
- 高频场景：100 次调用仅构造 1 个连接池客户端
"""
from __future__ import annotations

import asyncio

import pytest

from app.integrations import complaint_client as complaint_client_module
from app.integrations import qc_client as qc_client_module
from app.integrations.complaint_client import ComplaintClient
from app.integrations.qc_client import QcClient
from app.main import app, lifespan


class _FakeResp:
    """最小响应替身：qc/complaint 客户端只用 status_code/raise_for_status/json。"""

    def __init__(self, status_code: int = 200, body: dict | None = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._body


class _QueueClient:
    """httpx.AsyncClient 替身：构造计数 + 实例留痕 + 响应队列 + aclose 计数。

    responses 为空时返回 default_body 的成功响应，便于高频调用用例循环复用。
    """

    constructed = 0
    instances: list["_QueueClient"] = []
    default_body: dict = {"data": {"score": 90, "risk": "low"}}
    is_closed = False

    def __init__(self, **_kwargs) -> None:
        type(self).constructed += 1
        type(self).instances.append(self)
        self.calls: list[tuple] = []
        self.responses: list = []
        self.aclose_calls = 0

    def _next(self) -> _FakeResp:
        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _FakeResp(200, dict(type(self).default_body))

    async def get(self, url: str, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._next()

    async def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._next()

    async def aclose(self) -> None:
        self.aclose_calls += 1


@pytest.fixture(autouse=True)
def _reset_shared_state():
    """用例间隔离：清共享 client 字典与替身类计数，防止跨用例串扰。"""
    qc_client_module._shared_clients.clear()
    complaint_client_module._shared_clients.clear()
    _QueueClient.constructed = 0
    _QueueClient.instances = []
    _QueueClient.default_body = {"data": {"score": 90, "risk": "low"}}
    yield
    qc_client_module._shared_clients.clear()
    complaint_client_module._shared_clients.clear()


class TestT133QcClientShared:
    @pytest.mark.asyncio
    async def test_qc_100_calls_construct_single_client(self, monkeypatch):
        """高频场景：1 分钟内 100 次调用，连接池客户端构造次数为 1。"""
        monkeypatch.setattr(qc_client_module.httpx, "AsyncClient", _QueueClient)
        for i in range(100):
            c = QcClient(base_url="http://qc:8000")
            detail = await c.get_record(f"sess-{i:04d}")
            assert detail == {"score": 90, "risk": "low"}
        assert _QueueClient.constructed == 1

    @pytest.mark.asyncio
    async def test_qc_retry_reuses_same_client_in_loop(self, monkeypatch):
        """重试循环内部复用同一 client：3 次 attempt 仅构造 1 个客户端。"""
        monkeypatch.setattr(qc_client_module.httpx, "AsyncClient", _QueueClient)
        shared = qc_client_module.get_shared_qc_http_client()
        shared.responses = [
            RuntimeError("boom1"),
            RuntimeError("boom2"),
            _FakeResp(200, {"data": {"score": 1, "risk": "high"}}),
        ]
        c = QcClient(base_url="http://qc:8000", retry=3)
        detail = await c.get_record("sess-retry")
        assert detail == {"score": 1, "risk": "high"}
        assert _QueueClient.constructed == 1
        assert len(_QueueClient.instances) == 1
        assert len(shared.calls) == 3

    @pytest.mark.asyncio
    async def test_qc_explicit_injected_client_not_closed(self):
        """注入形态：实例使用注入 client 且不负责关闭（生命周期归注入方）。"""
        fake = _QueueClient()
        c = QcClient(base_url="http://qc:8000", client=fake)
        detail = await c.get_record("sess-inject")
        assert detail == {"score": 90, "risk": "low"}
        assert len(fake.calls) == 1
        assert fake.aclose_calls == 0

    @pytest.mark.asyncio
    async def test_qc_per_request_timeout_kept(self):
        """超时语义保持：per-request timeout 显式随请求传递。"""
        fake = _QueueClient()
        c = QcClient(base_url="http://qc:8000", timeout=2.5, client=fake)
        await c.get_record("sess-timeout")
        assert fake.calls[0][2]["timeout"] == 2.5

    @pytest.mark.asyncio
    async def test_qc_404_and_business_error_semantics(self, caplog):
        """既有语义回归：404 返回 None；业务错误码（A2005）返回 None 并告警。"""
        import logging

        fake = _QueueClient()
        fake.responses = [
            _FakeResp(404),
            _FakeResp(200, {"code": "A2005", "message": "no such record"}),
        ]
        c = QcClient(base_url="http://qc:8000", client=fake)
        with caplog.at_level(logging.WARNING):
            assert await c.get_record("sess-a") is None
            assert await c.get_record("sess-b") is None
        assert any(
            "qc_record_http_business_error" in r.getMessage() for r in caplog.records
        )


class TestT133ComplaintClientShared:
    @pytest.mark.asyncio
    async def test_complaint_100_calls_construct_single_client(self, monkeypatch):
        """complaint 同型：100 次逃生舱调用仅构造 1 个客户端。"""
        monkeypatch.setattr(complaint_client_module.httpx, "AsyncClient", _QueueClient)
        _QueueClient.default_body = {"data": {"complaint_id": "c-x", "task_status": "pending_review"}}
        shared = complaint_client_module.get_shared_complaint_http_client()
        for i in range(100):
            c = ComplaintClient(base_url="http://complaint:8000")
            cid = await c.create_complaint(case_id=f"case-{i:04d}", complaint_text="text")
            assert cid == "c-x"
        assert _QueueClient.constructed == 1
        assert len(shared.calls) == 100

    @pytest.mark.asyncio
    async def test_complaint_retry_failure_returns_empty_and_warns(self, monkeypatch, caplog):
        """重试耗尽语义保持：返回 {}、error 日志、warning 重试日志形态不变。"""
        import logging

        monkeypatch.setattr(complaint_client_module.httpx, "AsyncClient", _QueueClient)
        shared = complaint_client_module.get_shared_complaint_http_client()
        shared.responses = [RuntimeError("boom1"), RuntimeError("boom2")]
        c = ComplaintClient(base_url="http://complaint:8000", retry=2)
        with caplog.at_level(logging.WARNING):
            result = await c._request("POST", "/complaints", json_body={"a": 1})
        assert result == {}
        assert _QueueClient.constructed == 1
        assert len(shared.calls) == 2
        assert any("complaint_http_retry" in r.getMessage() for r in caplog.records)
        assert any("complaint_http_failed" in r.getMessage() for r in caplog.records)


class TestT133ShutdownRelease:
    @pytest.mark.asyncio
    async def test_aclose_shared_clients_idempotent(self):
        """aclose 幂等：重复调用仅关闭一次，共享字典清空。"""
        fake_qc = _QueueClient()
        fake_cp = _QueueClient()
        qc_client_module._shared_clients[asyncio.get_running_loop()] = fake_qc
        complaint_client_module._shared_clients[asyncio.get_running_loop()] = fake_cp

        await qc_client_module.aclose_shared_qc_http_client()
        await qc_client_module.aclose_shared_qc_http_client()
        await complaint_client_module.aclose_shared_complaint_http_client()
        await complaint_client_module.aclose_shared_complaint_http_client()

        assert fake_qc.aclose_calls == 1
        assert fake_cp.aclose_calls == 1
        assert len(qc_client_module._shared_clients) == 0
        assert len(complaint_client_module._shared_clients) == 0

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_closes_shared_clients(self, monkeypatch):
        """服务关停：lifespan shutdown 显式关闭出站 client，无悬挂连接。

        secrets 校验与本任务无关，monkeypatch 跳过（后台任务在 pytest 下
        由 skip_background_tasks 既有机制跳过）。
        """
        monkeypatch.setattr(
            "common.config.settings.validate_required_secrets", lambda cfg: None
        )
        loop = asyncio.get_running_loop()
        fake_qc = _QueueClient()
        fake_cp = _QueueClient()
        qc_client_module._shared_clients[loop] = fake_qc
        complaint_client_module._shared_clients[loop] = fake_cp

        async with lifespan(app):
            # startup 阶段不释放
            assert len(qc_client_module._shared_clients) == 1
            assert len(complaint_client_module._shared_clients) == 1

        assert fake_qc.aclose_calls == 1
        assert fake_cp.aclose_calls == 1
        assert len(qc_client_module._shared_clients) == 0
        assert len(complaint_client_module._shared_clients) == 0
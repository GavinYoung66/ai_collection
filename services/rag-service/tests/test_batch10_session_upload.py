"""批次 10：rag-service（T76/T77/T78）单测。

验收判据：
- 10.1 [T76]：mask_sensitive 收敛 common 单源——本地正则副本删除（形态守护），
  行为对齐 mask_text（掩码形态）；
- 10.2 [T77]：会话身份绑定——B 用户携带 A 的 session_id 被拒；匿名会话 TTL 缩短；
- 10.3 [T78]：上传体积流式校验——25MB 假 body 读入 < 21MB 时即 4xx（提前中断）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.requests import Request

from app.services.llm_service import mask_sensitive
from common.security.mask import mask_text


def _request(headers: dict[str, str], client=("10.0.0.1", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client})


class TestT76MaskSensitiveSingleSource:
    def test_local_regex_copies_removed(self):
        """形态守护：llm_service 本地正则副本零残留（MUST NOT 另立副本）。"""
        src = (
            Path(__file__).resolve().parents[1]
            / "app/services/llm_service.py"
        ).read_text(encoding="utf-8")
        assert "_PHONE_RE" not in src
        assert "_IDCARD_RE" not in src
        assert "_AMOUNT_RE" not in src
        assert "from common.security.mask import mask_text" in src

    def test_behavior_aligned_with_common_mask_text(self):
        """行为对齐 common mask_text（掩码形态，T70 统一正则）。"""
        assert mask_sensitive("联系 13812341234") == mask_text("联系 13812341234")
        assert "13812341234" not in mask_sensitive("联系 13812341234")


class TestT77SessionOwnerBinding:
    @pytest.fixture(autouse=True)
    def _clean_owners(self, monkeypatch):
        from app.api import deps as deps_mod

        deps_mod._SESSION_OWNERS.clear()
        monkeypatch.setattr(
            deps_mod, "_OWNER_TTL_SECONDS", deps_mod._OWNER_TTL_SECONDS
        )
        yield
        deps_mod._SESSION_OWNERS.clear()

    @pytest.mark.asyncio
    async def test_b_user_with_a_session_rejected(self, monkeypatch):
        """B 用户携带 A 的 session_id：owner 校验拒绝（读不到 A 的历史）。"""
        from app.api import deps as deps_mod
        from app.api.deps import get_session_id
        from common.errors.exceptions import BizException

        from common.security.rbac import create_token

        tok_a = create_token({"sub": "user_a"})
        tok_b = create_token({"sub": "user_b"})

        sid = get_session_id(_request({"X-Session-Id": "sess-shared",
                                       "Authorization": f"Bearer {tok_a}"}))
        monkeypatch.setattr(
            deps_mod, "_SESSION_OWNERS",
            {**deps_mod._SESSION_OWNERS, sid: ("user_a", time.time())},
        )
        with pytest.raises(BizException):
            get_session_id(_request({"X-Session-Id": sid,
                                     "Authorization": f"Bearer {tok_b}"}))

    @pytest.mark.asyncio
    async def test_same_owner_passes(self, monkeypatch):
        """owner 一致：重复访问通过（A 自己的会话）。"""
        from app.api.deps import get_session_id
        from common.security.rbac import create_token

        tok_a = create_token({"sub": "user_a"})
        sid = get_session_id(_request({"X-Session-Id": "sess-mine",
                                       "Authorization": f"Bearer {tok_a}"}))
        # 再次访问：同 owner 放行
        assert get_session_id(_request({"X-Session-Id": sid,
                                        "Authorization": f"Bearer {tok_a}"})) == sid

    @pytest.mark.asyncio
    async def test_anon_ttl_shortened(self, monkeypatch):
        """匿名会话 TTL 缩短：过期后条目自动失效（owner 归零）。"""
        from app.api import deps as deps_mod
        from app.api.deps import get_session_id, _owner_of

        get_session_id(_request({"X-Session-Id": "sess-anon"}))
        assert _owner_of("sess-anon") == "anon"
        # TTL 缩短为 0：立即过期
        monkeypatch.setattr(deps_mod, "_ANON_SESSION_TTL_SECONDS", 0.0)
        time.sleep(0.01)
        assert _owner_of("sess-anon") is None, "匿名条目应按缩短的 TTL 失效"


class FakeUploadFile:
    """mock UploadFile：分块返回 chunk（假 body），记录累计读入字节与每次 read 请求。"""

    def __init__(self, chunks: list[bytes], filename: str = "big.png", size=None):
        self._chunks = list(chunks)
        self.filename = filename
        self.size = size
        self.bytes_read = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        self.bytes_read += len(chunk)
        return chunk


class TestT78StreamingSizeCheck:
    @pytest.mark.asyncio
    async def test_25mb_body_aborts_before_21mb(self, monkeypatch, tmp_path):
        """25MB 假 body：累计读入 > max 但 ≤ max+chunk（21MB）时即 4xx——流式提前中断，
        不全量读入（S4 断言强化：跨限中断以读入字节数钉死，非仅断言抛异常）。"""
        from app.api import deps as deps_mod
        from app.api.deps import save_upload_file
        from common.errors.exceptions import BizException

        monkeypatch.setattr(deps_mod.settings, "KB_MAX_FILE_SIZE", 20 * 1024 * 1024)
        chunk = b"\x00" * deps_mod._UPLOAD_CHUNK_SIZE  # 1MB/块（与生产分块同粒度）
        big = FakeUploadFile([chunk] * 25)  # 25MB

        with pytest.raises(BizException):
            await save_upload_file(big)  # type: ignore[arg-type]
        max_size = 20 * 1024 * 1024
        assert big.bytes_read > max_size, "应在跨过上限后中断"
        assert big.bytes_read <= max_size + deps_mod._UPLOAD_CHUNK_SIZE, (
            "累计读入 MUST NOT 达到全量 25MB（流式提前中断）"
        )
        assert big.read_sizes and all(
            s == deps_mod._UPLOAD_CHUNK_SIZE for s in big.read_sizes
        ), "MUST 全程分块读取（无 size=-1 全量读调用）"

    @pytest.mark.asyncio
    async def test_staging_partial_write_cleaned(self, monkeypatch, tmp_path):
        """save_upload_to_staging 超限：部分写入残留被清理（不残留半截文件）。"""
        from app.api import deps as deps_mod
        from app.api.deps import save_upload_to_staging
        from common.errors.exceptions import BizException

        import uuid as uuid_mod

        monkeypatch.setattr(deps_mod.settings, "KB_MAX_FILE_SIZE", 20 * 1024 * 1024)
        monkeypatch.setattr(deps_mod.settings, "KB_INGEST_STAGING_DIR", str(tmp_path))
        chunk = b"\x00" * deps_mod._UPLOAD_CHUNK_SIZE
        big = FakeUploadFile([chunk] * 25)

        doc_id = uuid_mod.uuid4()
        with pytest.raises(BizException):
            await save_upload_to_staging(big, doc_id)  # type: ignore[arg-type]

        assert not (tmp_path / f"{doc_id}.png").exists(), (
            "超限中断后 staging 残留必须被清理"
        )
        # S4 断言强化：staging 路径同样流式提前中断（不全量读入）
        assert big.bytes_read <= 20 * 1024 * 1024 + deps_mod._UPLOAD_CHUNK_SIZE
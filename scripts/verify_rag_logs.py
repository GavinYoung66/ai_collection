"""本地 Mock 验证：上传大小校验（25MB→B0001）与解析超时（40s→30s 触发）。

用法：PYTHONDONTWRITEBYTECODE=1 /path/to/ai_collection/bin/python scripts/verify_rag_logs.py
说明：不依赖基础设施容器；外部依赖（DB/Milvus/ES/Embedding）用 Mock 替换，
      但 HTTP 层（路由/中间件/统一响应/鉴权/限流）与日志层（structlog）走真实链路。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "rag-service"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from structlog.testing import capture_logs  # noqa: E402

from app.api.deps import get_db, get_embed_client, get_es_client, get_milvus_client  # noqa: E402
from app.main import app  # noqa: E402
from app.parsers import ParserFactory  # noqa: E402
from common.security.rbac import create_token  # noqa: E402

HEADERS = {
    "Authorization": f"Bearer {create_token({'sub': 'admin', 'role': 'ADMIN'})}",
    "X-User-Id": "verify-user",
}


def _pick(events: list[dict], *names: str) -> list[dict]:
    return [e for e in events if e.get("event") in names]


def main() -> None:
    # 外部依赖全部 Mock（只验证 HTTP + 校验 + 日志链路）
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_milvus_client] = lambda: AsyncMock()
    app.dependency_overrides[get_es_client] = lambda: AsyncMock()
    app.dependency_overrides[get_embed_client] = lambda: AsyncMock()

    client = TestClient(app)
    ok = True

    # ============ 场景 1：上传 25MB 文件 → 预期 B0001 + 日志 upload_size_check_rejected ============
    print("=" * 70)
    print("[场景1] 上传 25MB 文件（限制 20MB）")
    big = b"x" * (25 * 1024 * 1024)
    with capture_logs() as cap1:
        resp1 = client.post(
            "/api/v1/kb/documents",
            files={"file": ("big_25mb.txt", big, "text/plain")},
            data={"category": "regulation"},
            headers=HEADERS,
        )
    body1 = resp1.json()
    print(f"HTTP 状态码: {resp1.status_code}")
    print(f"返回体: {json.dumps(body1, ensure_ascii=False)}")
    for ev in _pick(cap1, "upload_received", "upload_size_check_rejected", "upload_size_check_passed"):
        print(f"日志: {json.dumps(ev, ensure_ascii=False)}")
    pass1 = resp1.status_code == 400 and body1.get("code") == "B0001" and any(
        e.get("event") == "upload_size_check_rejected" for e in cap1
    )
    ok &= pass1
    print(f"场景1 结论: {'通过' if pass1 else '失败'}（code=B0001 + upload_size_check_rejected）")

    # ============ 场景 2：解析耗时 40s 的文档 → 预期 30s 超时 → A3001 + 日志 ingest_parse_timeout ============
    print("=" * 70)
    print("[场景2] 解析耗时 40s 的文档（超时阈值 30s，等待真实超时…）")

    class SlowParser:
        async def extract_text(self, path):  # noqa: N802
            await asyncio.sleep(40)
            return "解析太慢永远到不了这行"

    with (
        capture_logs() as cap2,
        patch.object(ParserFactory, "get_parser", staticmethod(lambda path: SlowParser())),
    ):
        resp2 = client.post(
            "/api/v1/kb/documents",
            files={"file": ("slow_doc.txt", b"hello rag", "text/plain")},
            data={"category": "regulation"},
            headers=HEADERS,
        )
    body2 = resp2.json()
    print(f"HTTP 状态码: {resp2.status_code}")
    print(f"返回体: {json.dumps(body2, ensure_ascii=False)}")
    for ev in _pick(cap2, "ingest_parse_start", "ingest_parse_timeout", "ingest_parse_done", "parse_failed"):
        print(f"日志: {json.dumps(ev, ensure_ascii=False)}")
    pass2 = resp2.status_code == 400 and body2.get("code") == "A3001" and any(
        e.get("event") == "ingest_parse_timeout" and e.get("timeout") == 30.0 for e in cap2
    )
    ok &= pass2
    print(f"场景2 结论: {'通过' if pass2 else '失败'}（code=A3001 + ingest_parse_timeout timeout=30.0）")

    app.dependency_overrides.clear()
    print("=" * 70)
    print("总体结论:", "全部通过" if ok else "存在失败")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

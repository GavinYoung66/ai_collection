#!/usr/bin/env python3
"""把 qc 演示约定的 data_materials 三份语料入库 rag-service。

category 必须为 script，与 RagClient.search 的 category 对齐。
校验通过即 processing；本脚本轮询至 active/failed。qc-service 不实现 ingest API。

用法（仓库根，rag 已起；宿主机默认 http://localhost:8002）：

  PYTHONPATH=. python scripts/qc/ingest_demo_materials.py

环境：
  RAG_BASE_URL  默认 http://localhost:8002（含或不含 /api/v1 均可）
  SMOKE_ADMIN_TOKEN  可选；缺省用 common.security.rbac.create_token(role=ADMIN)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / "data_materials"
FILES = (
    MATERIALS / "催收违规红线与标准合规话术手册（测试专用版）.pdf",
    MATERIALS / "催收行业自律公约.txt",
    MATERIALS / "前端催收合规细则200条.docx",
)
CATEGORY = "script"
POLL_TIMEOUT_S = float(os.environ.get("INGEST_POLL_TIMEOUT", "180"))


def _base() -> str:
    raw = os.environ.get("RAG_BASE_URL", "http://localhost:8002").rstrip("/")
    if raw.endswith("/api/v1"):
        return raw
    return f"{raw}/api/v1"


def get_token() -> str:
    if os.environ.get("SMOKE_ADMIN_TOKEN"):
        return os.environ["SMOKE_ADMIN_TOKEN"]
    sys.path.insert(0, str(ROOT))
    from common.security.rbac import create_token

    return create_token({"user_id": "qc_demo_ingest", "username": "qc_demo_ingest", "role": "ADMIN"})


def http(method: str, path: str, token: str, *, data: bytes | None = None, headers: dict | None = None):
    url = f"{_base()}{path}"
    hdrs = {"Authorization": f"Bearer {token}"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def multipart_upload(token: str, file_path: Path) -> tuple[int, dict]:
    boundary = f"----QcDemoIngest{uuid.uuid4().hex}"
    content = file_path.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="category"\r\n\r\n',
            f"{CATEGORY}\r\n".encode(),
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return http(
        "POST",
        "/kb/documents",
        token,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def wait_terminal(token: str, doc_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_S
    last: dict = {}
    while time.time() < deadline:
        _, body = http("GET", f"/kb/documents/{doc_id}", token)
        last = body.get("data") or {}
        if last.get("status") in {"active", "failed"}:
            return last
        time.sleep(1.0)
    return last


def main() -> int:
    missing = [str(path) for path in FILES if not path.is_file()]
    if missing:
        print("missing materials:", *missing, sep="\n  ")
        return 1
    token = get_token()
    print(f"BASE={_base()} category={CATEGORY}")
    failed = 0
    for path in FILES:
        status, body = multipart_upload(token, path)
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        doc_id = str((data or {}).get("doc_id") or "")
        ingest_status = (data or {}).get("status")
        print(f"upload {path.name} HTTP {status} status={ingest_status} doc_id={doc_id}")
        if status >= 400 or not doc_id:
            failed += 1
            print("  body=", body)
            continue
        if ingest_status != "processing":
            print("  warn: expected processing after validate; got", ingest_status)
        final = wait_terminal(token, doc_id)
        print(f"  poll status={final.get('status')} ingest_error={final.get('ingest_error')}")
        if final.get("status") != "active":
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

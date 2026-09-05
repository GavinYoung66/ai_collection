#!/usr/bin/env python3
"""async-kb-ingest-observability API 专项冒烟（task 7.2）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = os.environ.get("SMOKE_BASE_URL", "http://localhost:8002/api/v1")
SAMPLE = ROOT / "scripts/seed/sample_rules.txt"
DUP_NAME = "smoke_async_dup_check.txt"
QUERY = "催收合规管理制度（V1）"


def get_token() -> str:
    if os.environ.get("SMOKE_ADMIN_TOKEN"):
        return os.environ["SMOKE_ADMIN_TOKEN"]
    cmd = [
        "docker", "exec", "ai_collection-rag-service-1", "python", "-c",
        "from common.security.rbac import create_token; "
        "print(create_token({'user_id':'smoke','username':'smoke_tester','role':'admin'}), end='')",
    ]
    return subprocess.check_output(cmd, text=True).strip()


def http(method: str, path: str, token: str, *, data: bytes | None = None, headers: dict | None = None):
    url = f"{BASE}{path}"
    hdrs = {"Authorization": f"Bearer {token}"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


def multipart_upload(token: str, file_path: Path, filename: str | None = None) -> tuple[int, dict]:
    import uuid as _uuid

    boundary = f"----WebKitFormBoundary{_uuid.uuid4().hex}"
    name = filename or file_path.name
    content = file_path.read_bytes()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="category"\r\n\r\n',
        b"regulation\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return http(
        "POST",
        "/kb/documents",
        token,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def wait_terminal(token: str, doc_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        _, body = http("GET", f"/kb/documents/{doc_id}", token)
        last = body.get("data") or {}
        st = last.get("status")
        if st in ("active", "failed"):
            return last
        time.sleep(0.5)
    return last


def search_hits_doc(token: str, doc_id: str) -> bool:
    payload = json.dumps({"query": QUERY, "category": "regulation", "top_k": 20}).encode()
    for _ in range(5):
        _, body = http(
            "POST",
            "/kb/search",
            token,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        results = (body.get("data") or {}).get("results") or []
        if any(r.get("doc_id") == doc_id for r in results):
            return True
        time.sleep(0.5)
    return False


def delete_doc(token: str, doc_id: str) -> None:
    try:
        http("DELETE", f"/kb/documents/{doc_id}", token)
    except Exception:
        pass


def main() -> int:
    results: list[tuple[str, str, str]] = []
    token = get_token()
    print(f"BASE={BASE}")
    print(f"SAMPLE={SAMPLE}")
    print()

    # 1 早返回
    t0 = time.time()
    code, up = multipart_upload(token, SAMPLE, "smoke_async_early.txt")
    elapsed = time.time() - t0
    data = up.get("data") or {}
    ok1 = up.get("code") == "0" and data.get("status") == "processing" and bool(data.get("doc_id"))
    results.append((
        "1 早返回",
        "PASS" if ok1 and elapsed < 5 else "FAIL",
        f"code={up.get('code')} status={data.get('status')} doc_id={data.get('doc_id')} elapsed={elapsed:.2f}s",
    ))
    doc_id = data.get("doc_id", "")

    # 2 轮询（processing 快照 + 终态）
    proc_detail = {}
    if doc_id:
        _, snap = http("GET", f"/kb/documents/{doc_id}", token)
        proc_detail = snap.get("data") or {}
    terminal = wait_terminal(token, doc_id) if doc_id else {}
    ok2 = terminal.get("status") == "active" and (terminal.get("chunks_count") or 0) > 0
    results.append((
        "2 轮询",
        "PASS" if ok2 else "FAIL",
        f"processing_snapshot status={proc_detail.get('status')} stage={proc_detail.get('ingest_stage')}; "
        f"final status={terminal.get('status')} chunks={terminal.get('chunks_count')} "
        f"error={terminal.get('ingest_error')}",
    ))

    # 3 防重
    code3a, up3 = multipart_upload(token, SAMPLE, DUP_NAME)
    doc3 = (up3.get("data") or {}).get("doc_id", "")
    time.sleep(0.2)
    _, dup = multipart_upload(token, SAMPLE, DUP_NAME)
    dup_code = dup.get("code")
    dup_msg = dup.get("message", "")
    ok3 = dup_code == "B0001" and "入库" in dup_msg
    results.append((
        "3 防重",
        "PASS" if ok3 else "FAIL",
        f"first code={code3a} doc={doc3}; dup code={dup_code} message={dup_msg[:60]}",
    ))
    if doc3:
        wait_terminal(token, doc3, timeout_s=30)

    # 4 不可检索
    code4, up4 = multipart_upload(token, SAMPLE, "smoke_async_search.txt")
    doc4 = (up4.get("data") or {}).get("doc_id", "")
    hit_while_processing = False
    saw_processing = False
    if doc4:
        for _ in range(20):
            _, snap4 = http("GET", f"/kb/documents/{doc4}", token)
            st4 = (snap4.get("data") or {}).get("status")
            if st4 == "processing":
                saw_processing = True
                hit_while_processing = search_hits_doc(token, doc4)
                if hit_while_processing:
                    break
            if st4 in ("active", "failed"):
                break
            time.sleep(0.1)
    terminal4 = wait_terminal(token, doc4) if doc4 else {}
    hit_after_active = search_hits_doc(token, doc4) if terminal4.get("status") == "active" else None
    ok4 = (not hit_while_processing) and (hit_after_active is True)
    results.append((
        "4 不可检索",
        "PASS" if ok4 else "FAIL",
        f"saw_processing={saw_processing} processing_hit={hit_while_processing}; "
        f"final={terminal4.get('status')} active_hit={hit_after_active}",
    ))

    # 5 reindex
    ok5 = False
    reindex_note = "skipped (no active doc)"
    if doc_id and terminal.get("status") == "active":
        _, re = http("POST", f"/kb/documents/{doc_id}/reindex", token)
        rd = re.get("data") or {}
        ok5 = re.get("code") == "0" and rd.get("assets_rebuilt") is False
        reindex_note = f"code={re.get('code')} assets_rebuilt={rd.get('assets_rebuilt')} status={rd.get('status')}"
    results.append(("5 reindex", "PASS" if ok5 else "FAIL", reindex_note))

    # cleanup
    for did in {doc_id, doc3, doc4}:
        if did:
            delete_doc(token, did)

    print("=== async-kb-ingest-observability API Smoke ===")
    passed = failed = 0
    for name, status, evidence in results:
        print(f"[{status}] {name}: {evidence}")
        if status == "PASS":
            passed += 1
        else:
            failed += 1
    print(f"\nSummary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

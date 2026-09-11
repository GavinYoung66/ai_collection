#!/usr/bin/env python3
"""M1 词库同构 mock：只提供 rag current.dict。禁止 /kb/search。"""
from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DICT_PATH = "/api/v1/sensitive-words/current.dict"
DICT_FILE = Path(__file__).resolve().with_name("current.dict")
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/"}:
            body = b'{"status":"ok","service":"rag-dict-mock"}'
            self._send(200, "application/json", body)
            return
        if path != DICT_PATH:
            self.send_error(404, "Not Found")
            return
        text = DICT_FILE.read_text(encoding="utf-8")
        self._send(200, "text/plain; charset=utf-8", text.encode("utf-8"))

    def do_POST(self) -> None:
        self.send_error(404, "Not Found")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()

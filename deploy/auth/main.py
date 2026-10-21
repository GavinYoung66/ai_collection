"""极薄鉴权 sidecar（api-gateway-hardening D3 / tasks 4.1）。

契约（specs/security/authn「鉴权 sidecar 契约」）：
- 暴露 /internal/authz 供 nginx auth_request 子请求调用。
- 验证逻辑 100% 来自 common.security.authn.authenticate，
  本文件 MUST NOT 自行导入 jose / 解码 JWT / 比对 Key（单一实现）。
- ok=True  → 204 No Content，响应头回传 X-User-Id / X-User-Role 供
              nginx auth_request_set 捕获后透传给后端。
- ok=False → 401，body 仅带调试用 reason（不含业务错误码；客户端信封
              由 nginx error_page @auth_required 负责，本 sidecar MUST NOT
              引入 code/message/data 等业务信封字段）。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, Response

from common.security.authn import AuthResult, authenticate

# 独立审计日志：不依赖 uvicorn.error 等 ASGI 专属 logger，避免更换服务器
# （hypercorn/gunicorn）时日志丢失。显式 StreamHandler 写 stderr，docker logs 可见。
logger = logging.getLogger("auth_sidecar")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False

app = FastAPI(title="api-gateway auth sidecar")


@app.api_route(
    "/internal/authz",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
def authz(authorization: str = Header(default="", alias="Authorization")) -> Response:
    """nginx auth_request 子请求沿用原始方法，故全方法受理。"""
    result: AuthResult = authenticate(authorization)
    if result.ok:
        # 审计日志：记录回传身份，便于经 sidecar 日志确认身份透传链路。
        logger.info("authz ok: user_id=%s role=%s", result.user_id, result.role)
        # 204 无 body 但可携带响应头；nginx 经 auth_request_set 捕获。
        return Response(
            status_code=204,
            headers={
                "X-User-Id": result.user_id or "",
                "X-User-Role": result.role or "",
            },
        )
    logger.info("authz reject: reason=%s", result.reason)
    # 仅调试用 reason，不含业务错误码（业务信封由 nginx @auth_required 产出）。
    return Response(
        status_code=401,
        media_type="application/json",
        content=f'{{"reason":{_json_str(result.reason or "unauthorized")}}}',
    )


def _json_str(value: str) -> str:
    """极简 JSON 字符串转义，避免为调试信封引入额外依赖。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

#!/usr/bin/env bash
# 冒烟共享认证帮助（T02 冒烟补凭证）。
#
# 背景：complaints / qc records 含 PII，auth_required=true 时要求 JWT；
# 冒烟脚本须同步携带凭证（design D6/T02 验收项）。全仓无登录端点，
# 沿用 qc 测试既有实践：用 common.security.rbac.create_token 自签 JWT。
#
# 行为约定：
#   - 服务端验签同读 JWT_SECRET（CI/本地同源注入），自签 token 在认证开启时可用；
#   - iss/aud 若在 settings 配置则一并写入载荷，避免手动校验不匹配；
#   - 找不到 python/jose/JWT_SECRET 时输出空串，冒烟以匿名降级
#     （本地关闭认证场景零回归；认证开启时对应断言会以 401 显式失败，不静默假通过）。

smoke_pick_python() {
    local conda_base
    if [ -n "${PYTHON:-}" ]; then
        echo "$PYTHON"
        return
    fi
    if [ -n "${CONDA_PREFIX:-}" ] && [ -x "${CONDA_PREFIX}/bin/python" ]; then
        echo "${CONDA_PREFIX}/bin/python"
        return
    fi
    conda_base="$(conda info --base 2>/dev/null || true)"
    if [ -n "$conda_base" ] && [ -x "${conda_base}/envs/ai_collection/bin/python" ]; then
        echo "${conda_base}/envs/ai_collection/bin/python"
        return
    fi
    if [ -x "${HOME}/miniconda3/envs/ai_collection/bin/python" ]; then
        echo "${HOME}/miniconda3/envs/ai_collection/bin/python"
        return
    fi
    command -v python3 || command -v python
}

# 输出 "Authorization: Bearer <jwt>"；失败输出空串（不抛错，调用方用 ${VAR:+} 接线）。
smoke_auth_header() {
    local root pybin token
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    pybin="$(smoke_pick_python 2>/dev/null || true)"
    [ -n "$pybin" ] || return 0
    token="$(PYTHONPATH="$root" "$pybin" - <<'PY' 2>/dev/null
from uuid import uuid4

from common.config.settings import settings
from common.security.rbac import create_token

data = {"sub": str(uuid4()), "role": "admin"}
if settings.JWT_ISSUER:
    data["iss"] = settings.JWT_ISSUER
if settings.JWT_AUDIENCE:
    data["aud"] = settings.JWT_AUDIENCE
print(create_token(data))
PY
)" || return 0
    [ -n "$token" ] && printf 'Authorization: Bearer %s' "$token"
    return 0
}

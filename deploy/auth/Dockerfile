# api-gateway 鉴权 sidecar（api-gateway-hardening D3 / tasks 4.1）
# 极薄 FastAPI，验证逻辑完全复用 common.security.authn（单一实现）。
# common 代码由 compose 以 volume 挂载运行时注入，不 COPY 进镜像。
FROM python:3.11-slim

WORKDIR /app

COPY deploy/auth/main.py /app/main.py

# fastapi/uvicorn 提供 HTTP 服务；pydantic-settings 供 common.config.settings 读取；
# python-jose[cryptography] 是 common.security.rbac 验签的运行时依赖。
# sqlalchemy/asyncpg/passlib 是 F0 登录（查 users 表 + bcrypt 校验）的运行时依赖，
# 版本对齐根 requirements.lock，避免与其它服务的依赖版本漂移。
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    pydantic-settings \
    "python-jose[cryptography]" \
    "sqlalchemy[asyncio]==2.0.23" \
    "asyncpg==0.29.0" \
    "passlib[bcrypt]==1.7.4" \
    "bcrypt==4.0.1"

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

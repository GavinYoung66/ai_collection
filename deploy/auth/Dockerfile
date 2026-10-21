# api-gateway 鉴权 sidecar（api-gateway-hardening D3 / tasks 4.1）
# 极薄 FastAPI，验证逻辑完全复用 common.security.authn（单一实现）。
# common 代码由 compose 以 volume 挂载运行时注入，不 COPY 进镜像。
FROM python:3.11-slim

WORKDIR /app

COPY deploy/auth/main.py /app/main.py

# fastapi/uvicorn 提供 HTTP 服务；pydantic-settings 供 common.config.settings 读取；
# python-jose[cryptography] 是 common.security.rbac 验签的运行时依赖。
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    pydantic-settings \
    "python-jose[cryptography]"

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

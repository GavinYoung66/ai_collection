FROM python:3.11.15-slim
WORKDIR /workspace
# apt 源换阿里云镜像（debian 官方源在沙箱网络下过慢/易卡）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl redis-tools \
    && rm -rf /var/lib/apt/lists/*
# token 生成依赖（common.security.rbac → PyJWT + pydantic-settings，仅拉纯 python 包）
RUN pip install --no-cache-dir "PyJWT==2.15.1" "pydantic-settings==2.1.0"
CMD ["bash", "scripts/smoke/smoke_rag_v4.sh"]

FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于健康检查，gcc + libc6-dev 兜底 arm64 无预编译 wheel 的包
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
COPY services/mcp-gateway/requirements.runtime.txt ./requirements.runtime.txt
# 两段安装：先装 lock 基础依赖（排除 rocketmq-python-client——与 langfuse otlp 小版本冲突，
# 网关不依赖 RocketMQ 故无需补装），再装 runtime 增量。
# mcp SDK 会把 pydantic/uvicorn/httpx/pydantic-settings 升到其要求版本，仅影响本容器。
RUN grep -vE '^rocketmq-python-client' requirements.lock > /tmp/requirements.base.lock \
    && pip install --no-cache-dir -r /tmp/requirements.base.lock \
        -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.runtime.txt \
        -i https://mirrors.aliyun.com/pypi/simple/
COPY common/ ./common/
COPY services/mcp-gateway/app/ ./app/
ENV PYTHONPATH=/app
EXPOSE 9000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]

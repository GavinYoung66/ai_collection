FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于健康检查，gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
# pip 走阿里云镜像。rocketmq-python-client 与 langfuse 对 otlp 小版本冲突：
# 先装 lock（排除 rocketmq），再单独装 rocketmq + otlp>=1.33.1 对齐 langfuse。
RUN grep -vE '^rocketmq-python-client' requirements.lock > /tmp/requirements.base.lock \
    && pip install --no-cache-dir -r /tmp/requirements.base.lock \
        -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir \
        'rocketmq-python-client==5.1.1' \
        'opentelemetry-exporter-otlp>=1.33.1' \
        -i https://mirrors.aliyun.com/pypi/simple/ \
    && python -c "import rocketmq; print('rocketmq ok')"
COPY common/ ./common/
# 只拷 app 子目录到 /app/app/（原写法嵌套成 /app/app/app/，uvicorn app.main:app 找不到入口）
COPY services/data-platform/app/ ./app/
COPY services/data-platform/scripts/ ./scripts/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于 HEALTHCHECK，gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel）
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
COPY services/qc-service/app/ ./app/
ENV PYTHONPATH=/app
# 容器内 uvicorn 监听 8000；compose 映射宿主机 8003:8000。EXPOSE 与 CMD 一致。
# 启动拉词库走 lifespan WordLoader.load_once（GET current.dict），禁止另写知识库检索入口。
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

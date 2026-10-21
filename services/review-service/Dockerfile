FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于健康检查，gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
# pip 走阿里云镜像（官方 PyPI 在本机网络下过慢/易抖动）
# 排除 rocketmq-python-client：脚手架不引用 rocketmq/eventbus（app 源码零引用）。
# 全量安装会确定性失败——rocketmq 5.1.1 经 opentelemetry-exporter-otlp 1.33.0 钉死
# proto-http==1.33.0，与 lock 中 langfuse 4.15.2（要求 proto-http>=1.33.1）ResolutionImpossible；
# 其 grpcio-tools 传递依赖在 arm64 还会回溯至无 aarch64 wheel 的 1.51.3 触发源码构建失败。
# 处理方式与 mcp-gateway Dockerfile 一致（2026-10-07 4.1 双架构实测后最小修复）。
RUN grep -vE '^rocketmq-python-client' requirements.lock > /tmp/requirements.base.lock \
    && pip install --no-cache-dir -r /tmp/requirements.base.lock \
        -i https://mirrors.aliyun.com/pypi/simple/
COPY common/ ./common/
# 只拷 app 子目录到 /app/app/（原写法嵌套成 /app/app/app/，uvicorn app.main:app 找不到入口）
COPY services/review-service/app/ ./app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于健康检查，gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
# pip 走阿里云镜像（官方 PyPI 在本机网络下过慢/易抖动）
RUN pip install --no-cache-dir -r requirements.lock -i https://mirrors.aliyun.com/pypi/simple/
COPY common/ ./common/
# 只拷 app 子目录到 /app/app/（原写法嵌套成 /app/app/app/，uvicorn app.main:app 找不到入口）
COPY services/case-service/app/ ./app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

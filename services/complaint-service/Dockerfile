FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像；curl 用于健康检查，gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
COPY services/complaint-service/requirements.runtime.txt ./requirements.runtime.txt
# pip 走阿里云镜像（官方 PyPI 在本机网络下过慢/易抖动）
# 全局 lock 仍注释 langgraph；complaint 真图依赖由 requirements.runtime.txt 单独安装
RUN pip install --no-cache-dir -r requirements.lock -i https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.runtime.txt -i https://mirrors.aliyun.com/pypi/simple/
COPY common/ ./common/
# 只拷 app 子目录到 /app/app/（原写法嵌套成 /app/app/app/，uvicorn app.main:app 找不到入口）
COPY services/complaint-service/app/ ./app/
# Mock MCP/LLM 在 USE_MOCK_*=true 时从 tests.mock 懒加载（dev 默认）
COPY services/complaint-service/tests/__init__.py ./tests/__init__.py
COPY services/complaint-service/tests/mock/ ./tests/mock/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

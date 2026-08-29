# Gradio 浏览器前端独立镜像（V5，仅内部网络，端口 7860）。
# 注意：不安装全局 requirements.lock —— gradio 要求 fastapi>=0.115.2 / pillow<13，
# 与全局锁 fastapi==0.104.1 / pdfplumber 的 Pillow>=12.2.0 硬冲突（见 deploy/gradio/requirements.txt 头注）；
# gradio 前端仅依赖 gradio + httpx，独立锁定零影响 V4 七个服务。
FROM python:3.11.15-slim
WORKDIR /app
# curl 供 compose healthcheck；apt 走阿里云镜像（官方 Debian 源在本机网络下过慢/易抖动，
# 与下方 pip 走阿里云镜像策略一致；兼容 bookworm deb822 与经典 sources.list 两种格式）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/gradio/requirements.txt ./requirements.txt
# pip 走阿里云镜像（与全局既有镜像一致，官方 PyPI 在本机网络下过慢/易抖动）
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
COPY services/rag-service/gradio/ ./app/
ENV GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_SERVER_PORT=7860 \
    # 与 rag-service 共享挂载的数据卷（图片/表格预览源；compose 挂载 rag_assets）
    KB_ASSETS_DIR=/app/data/assets
EXPOSE 7860
CMD ["python", "/app/app/app.py"]

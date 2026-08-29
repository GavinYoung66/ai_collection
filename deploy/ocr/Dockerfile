# 原生 arm64 OCR 镜像（避免 amd64 paddle 在 Apple Silicon 上 SIGILL）
# 基础镜像优先走 DaoCloud 镜像加速
FROM docker.m.daocloud.io/library/python:3.10-slim

WORKDIR /app

RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 libglib2.0-0 libsm6 libxext6 libxrender1 libgl1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# paddle 官方 CPU wheel（含 aarch64）；其余走阿里云 PyPI
RUN pip install --no-cache-dir \
    -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
    --trusted-host www.paddlepaddle.org.cn \
    paddlepaddle==2.6.1 \
    && pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    "numpy<2" \
    fastapi==0.104.1 \
    uvicorn==0.24.0 \
    python-multipart==0.0.9 \
    paddleocr==2.7.0.3 \
    pdf2image==1.17.0 \
    pillow==10.2.0

COPY app.py /app/app.py
COPY requirements.txt /app/requirements.txt

EXPOSE 8200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8200"]

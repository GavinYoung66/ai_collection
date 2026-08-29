FROM python:3.11.15-slim
WORKDIR /app
# apt 源换阿里云镜像（debian 官方源在沙箱网络下过慢/易卡）；
# gcc + libc6-dev 用于编译 pyahocorasick（arm64 无预编译 wheel，且 slim 镜像缺 stdlib.h）
RUN sed -i 's|deb.debian.org/debian-security|mirrors.aliyun.com/debian-security|g; s|deb.debian.org/debian|mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl gcc libc6-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
# pip 走阿里云镜像（与上方 apt 镜像一致；官方 PyPI 在本机网络下过慢/易抖动，
# 曾导致 pydantic-core 索引拉取失败 "from versions: none"）
RUN pip install --no-cache-dir -r requirements.lock -i https://mirrors.aliyun.com/pypi/simple/
COPY common/ ./common/
# 只拷 app 子目录到 /app/app/：若 COPY services/rag-service/ ./app/ 会嵌套成 /app/app/app/main.py，
# 导致 uvicorn app.main:app 找不到入口（7 个服务 Dockerfile 同款存量 bug，本文件已修）
COPY services/rag-service/app/ ./app/
COPY services/rag-service/conf/ ./conf/
COPY services/rag-service/data/ ./data/
# 目标路径必须带 rag/ 子目录：entrypoint 按 scripts/rag/es_init_v4.py 调用，
# 若只 COPY 到 ./scripts/ 会把文件内容直接平铺进 /app/scripts/
COPY scripts/rag/ ./scripts/rag/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY services/rag-service/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PYTHONPATH=/app
# V2 功能开关默认全 false（=V1 行为），k8s ConfigMap 可通过 env 覆盖
ENV KB_QUERY_REWRITE_ENABLED=false \
    KB_ROUTING_ENABLED=false \
    KB_WEIGHTING_ENABLED=false \
    KB_ROW_LEVEL_ENABLED=false \
    KB_MULTITURN_ENABLED=false \
    KB_EMBED_DEGRADED_KEEP_TEXT=false \
    ES_DUAL_WRITE=false \
    KB_OCR_ASR_DEGRADED_KEEP_TEXT=false \
    KB_ES_DEGRADED_KEEP_MILVUS=false
# V4 功能开关默认全 false（=V3 行为）
ENV KB_SUMMARY_ENABLED=false \
    KB_SUMMARY_ES_BOOST=1.0 \
    KB_SUMMARY_MAX_LEN=200 \
    KB_ASR_OPENING_TRIM_ENABLED=false
# V5 功能开关与配置默认（=V4 行为）；LLM_MODEL / LLM_TIMEOUT 复用既有配置不改默认值（query_rewrite 依赖）
ENV KB_LLM_ANSWER_ENABLED=false \
    LLM_PROVIDER=dashscope \
    LLM_MAX_CONCURRENCY=4 \
    LLM_LOCAL_BASE_URL= \
    LLM_LOCAL_API_KEY= \
    KB_IMAGE_EXTRACT_ENABLED=false \
    KB_TABLE_EXTRACT_ENABLED=false \
    KB_EXCEL_SHEET_ALL=true \
    KB_EXCEL_HEADER_ROW=1 \
    KB_ASR_AMR_WMA_ENABLED=false \
    KB_ASSETS_DIR=data/assets \
    KB_ASSETS_MAX_SIZE=1024
# 文档入库限制（对齐 DD-03 §8）
ENV KB_MAX_FILE_SIZE=20971520 \
    KB_PARSE_TIMEOUT=30 \
    KB_HEALTH_DEGRADED_MODE=false
CMD ["/entrypoint.sh"]

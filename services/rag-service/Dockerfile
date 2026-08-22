FROM python:3.11.15-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY common/ ./common/
COPY services/rag-service/ ./app/
COPY services/rag-service/conf/ ./conf/
COPY scripts/rag/ ./scripts/
ENV PYTHONPATH=/app
# V2 功能开关默认全 false（=V1 行为），k8s ConfigMap 可通过 env 覆盖
ENV KB_QUERY_REWRITE_ENABLED=false \
    KB_ROUTING_ENABLED=false \
    KB_WEIGHTING_ENABLED=false \
    KB_ROW_LEVEL_ENABLED=false \
    KB_MULTITURN_ENABLED=false \
    KB_EMBED_DEGRADED_KEEP_TEXT=false \
    ES_DUAL_WRITE=false
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

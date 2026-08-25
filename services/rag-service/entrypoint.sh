#!/bin/sh
set -e
# rag-service 容器入口：ES 索引幂等初始化 → Alembic 到 0007（含 kb_documents.summary）→ 启动 API。
# SKIP_ES_INIT=1 时跳过 ES 索引初始化；SKIP_MIGRATE=1 时跳过迁移（冒烟 sidecar 复用同一镜像）。
if [ "${SKIP_ES_INIT:-0}" != "1" ]; then
  echo "[entrypoint] es_init_v4 (idempotent, ensure kb_bm25 doc_summary)"
  python scripts/rag/es_init_v4.py
fi
if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
  echo "[entrypoint] alembic upgrade head"
  alembic upgrade head
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

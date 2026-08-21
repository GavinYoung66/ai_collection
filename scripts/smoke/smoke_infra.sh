#!/bin/bash
# 基础设施连通性冒烟测试
# 验证 6 容器 + embedding + rerank-service 全部连通可用

set -e

PASS=0
FAIL=0

check() {
    local name=$1
    local cmd=$2
    if eval "$cmd" >/dev/null 2>&1; then
        echo "[PASS] $name"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $name"
        FAIL=$((FAIL + 1))
    fi
}

echo "========================================"
echo "  Infrastructure Smoke Test"
echo "========================================"

# PostgreSQL
check "PG 连通" "pg_isready -h localhost -p 5432 -U ai_collection"
check "PG uuid-ossp" "psql -h localhost -U ai_collection -d ai_collection -c \"SELECT 1 FROM pg_extension WHERE extname='uuid-ossp'\" | grep -q 1"
check "PG dw schema" "psql -h localhost -U ai_collection -d ai_collection -c \"SELECT 1 FROM information_schema.schemata WHERE schema_name='dw'\" | grep -q 1"

# Redis
check "Redis PING" "redis-cli -h localhost -p 6379 ping | grep -q PONG"

# Milvus
check "Milvus 连通" "curl -sf http://localhost:9091/healthz >/dev/null"

# Elasticsearch
check "ES cluster health" "curl -sf http://localhost:9200/_cluster/health >/dev/null"

# Embedding
check "Embedding /health" "curl -sf http://localhost:8100/health >/dev/null"

# Rerank
check "Rerank /health" "curl -sf http://localhost:8101/health >/dev/null"

# MinIO
check "MinIO 连通" "curl -sf http://localhost:9000/minio/health/live >/dev/null"

echo "========================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi

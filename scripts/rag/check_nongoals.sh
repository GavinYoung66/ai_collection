#!/usr/bin/env bash
# §16.15 依赖与 SDK 防呆脚本（Non-Goals 防护）
# 对齐 design.md Non-Goals §1：LangSmith/langchain 归 V3，V2 禁止接入
#
# 断言：
#   ① 全局 requirements.in / requirements.lock 不含 langsmith / langsmith-sdk / openinference / wandb
#   ② rag-service 源码不含 from langsmith / import langsmith / LangSmithTracer / tracing_enabled
#
# 用法：bash scripts/rag/check_nongoals.sh
# 退出码：0=通过，1=有违规

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VIOLATIONS=0

echo "=== Non-Goals 防呆校验 ==="

# ① 全局 requirements 文件审计
FORBIDDEN_DEPS='langsmith|langsmith-sdk|openinference|wandb'

echo "--- 检查 requirements.in / requirements.lock ---"
for req_file in "$PROJECT_ROOT/requirements.in" "$PROJECT_ROOT/requirements.lock"; do
    if [ -f "$req_file" ]; then
        matches=$(grep -iE "$FORBIDDEN_DEPS" "$req_file" || true)
        if [ -n "$matches" ]; then
            echo "VIOLATION: $req_file 含禁止依赖（见 Non-Goals §1：LangSmith 归 V3）"
            echo "$matches"
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    fi
done

# ② rag-service 源码搜索
TARGET_DIR="$PROJECT_ROOT/services/rag-service"
FORBIDDEN_IMPORTS='from langsmith|import langsmith|LangSmithTracer|tracing_enabled|from langchain|import langchain'

echo "--- 检查 rag-service 源码: $TARGET_DIR ---"
matches=$(grep -rnE "$FORBIDDEN_IMPORTS" "$TARGET_DIR" --include='*.py' || true)
if [ -n "$matches" ]; then
    echo "VIOLATION: rag-service 源码含禁止的追踪/评估 SDK 引用"
    echo "$matches"
    VIOLATIONS=$((VIOLATIONS + 1))
fi

if [ "$VIOLATIONS" -gt 0 ]; then
    echo ""
    echo "FAIL: Non-Goals 防呆校验失败（$VIOLATIONS 处违规）"
    echo "提示：见 design.md Non-Goals §1：LangSmith/langchain 归 V3，V2 禁止接入"
    exit 1
fi

echo "PASS: Non-Goals 防呆校验通过（0 违规）"
exit 0

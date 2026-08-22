#!/usr/bin/env bash
# §16.14 P0 错误码门禁脚本
# 对齐 PRD §7 + step6/01 强制 7：rag-service 错误码白名单校验
#
# 白名单（rag-service 允许使用的错误码）：
#   A3001, A3002, A3003, A3005, A3006
#   B0001, B0002, B0003, B0004, B0005, B0006, B0007, B0008, B0009, B0010
#
# 禁止出现：
#   - A3004（检索 score 不足，rag-service 不使用）
#   - A3007+ （超出 rag-service 域）
#   - B0011+ （超出系统级定义）
#   - A[124-7]xxx（跨域错误码：A1=data-platform, A2=qc, A4=call, A5=case, A6=complaint, A7=review）
#
# 用法：bash scripts/rag/check_error_codes.sh
# 退出码：0=通过，1=有违规

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET_DIR="$PROJECT_ROOT/services/rag-service"

# 白名单正则：A3001|A3002|A3003|A3005|A3006|B0001..B0010
WHITELIST_REGEX='A300[12356]|B00(0[1-9]|10)'

# 搜索所有 ErrorCode.xxx 和 "Axxxx"/"Bxxxx" 字面量引用
VIOLATIONS=0

echo "=== 错误码门禁校验：$TARGET_DIR ==="

# 1. 搜索 ErrorCode.Axxxx / ErrorCode.Bxxxx 引用
while IFS= read -r line; do
    # 提取错误码
    code=$(echo "$line" | grep -oE 'ErrorCode\.(A[0-9]{4}|B[0-9]{4})' | grep -oE '[AB][0-9]{4}')
    if [ -n "$code" ]; then
        if ! echo "$code" | grep -qE "^($WHITELIST_REGEX)$"; then
            echo "VIOLATION: 错误码 $code 不在 rag-service 白名单中"
            echo "  位置: $line"
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    fi
done < <(grep -rnE 'ErrorCode\.(A[0-9]{4}|B[0-9]{4})' "$TARGET_DIR" --include='*.py' || true)

# 2. 搜索字符串字面量 "Axxxx" / "Bxxxx"（排除注释和 codes.py 定义文件）
while IFS= read -r line; do
    code=$(echo "$line" | grep -oE '"(A[0-9]{4}|B[0-9]{4})"' | tr -d '"')
    if [ -n "$code" ]; then
        if ! echo "$code" | grep -qE "^($WHITELIST_REGEX)$"; then
            echo "VIOLATION: 字面量错误码 \"$code\" 不在 rag-service 白名单中"
            echo "  位置: $line"
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    fi
done < <(grep -rnE '"(A[0-9]{4}|B[0-9]{4})"' "$TARGET_DIR" --include='*.py' | grep -v 'codes.py' || true)

# 3. 搜索跨域错误码 A1xxx/A2xxx/A4xxx/A5xxx/A6xxx/A7xxx
CROSS_DOMAIN=$(grep -rnE 'ErrorCode\.A[124-7][0-9]{3}|"A[124-7][0-9]{3}"' "$TARGET_DIR" --include='*.py' | grep -v 'codes.py' || true)
if [ -n "$CROSS_DOMAIN" ]; then
    echo "VIOLATION: 发现跨域错误码（A1/A2/A4/A5/A6/A7xxx）"
    echo "$CROSS_DOMAIN"
    VIOLATIONS=$((VIOLATIONS + 1))
fi

if [ "$VIOLATIONS" -gt 0 ]; then
    echo ""
    echo "FAIL: 发现 $VIOLATIONS 处错误码违规，不允许合并"
    exit 1
fi

echo "PASS: rag-service 错误码白名单校验通过（0 违规）"
exit 0

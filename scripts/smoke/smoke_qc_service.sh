#!/usr/bin/env bash
# 已废弃口径（曾探 /api/v1/health 与顶层 healthy）。
# 请使用 scripts/smoke/smoke_qc.sh（health → seed → replay/raw → records → alerts；不起 call-service）。
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/smoke_qc.sh" "$@"

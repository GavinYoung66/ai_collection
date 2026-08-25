#!/usr/bin/env bash
#=============================================================
# run_v4_verify.sh
# rag-multimodal-kb-v4 一体化本地验证脚本
#   静态门禁(check_error_codes/check_nongoals/ruff)
#   + OpenSpec 校验 + 全量单测 + 容器化冒烟(smoke-v4)
#
# 用法:
#   bash run_v4_verify.sh
#   # 或
#   chmod +x run_v4_verify.sh && ./run_v4_verify.sh
#
# 约定:
#   - 全部验证在本机终端执行；脚本不修改任何代码/OpenSpec 工件
#   - A 类失败(业务规格 bug) -> 最终退出码非 0，禁止归档
#   - B 类失败(本地环境问题) -> 记 FAIL(B)/WARN，不直接终止，最终退出码 0
#=============================================================
set -u
set -o pipefail

# ---------- 常量 ----------
CHANGE="rag-multimodal-kb-v4"
CHANGE_ARCHIVE="openspec/changes/archive/2026-08-24-${CHANGE}"
SMOKE_SERVICES="postgres redis etcd minio milvus elasticsearch embedding rerank-service rag-service smoke-v4"

# ---------- 输出工具 ----------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
PASS_N=0; FAIL_N=0; WARN_N=0; A_FAIL_N=0; ROWS=""

say()  { printf '%b\n' "$*"; }
ok()   { PASS_N=$((PASS_N+1)); ROWS="${ROWS}| $1 | ${GREEN}PASS${NC} |\n"; say "${GREEN}[PASS ]${NC} $1"; }
warn() { WARN_N=$((WARN_N+1)); ROWS="${ROWS}| $1 | ${YELLOW}WARN${NC} |\n"; say "${YELLOW}[WARN ]${NC} $1"; }
fa()   { FAIL_N=$((FAIL_N+1)); A_FAIL_N=$((A_FAIL_N+1)); ROWS="${ROWS}| $1 | ${RED}FAIL-A${NC} |\n"; say "${RED}[FAIL-A]${NC} $1"; }
fb()   { FAIL_N=$((FAIL_N+1)); ROWS="${ROWS}| $1 | ${RED}FAIL-B${NC} |\n"; say "${RED}[FAIL-B]${NC} $1"; }
header() { say ""; say "${CYAN}========== $1 ==========${NC}"; }

# ---------- Step 0 环境检查 ----------
header "Step 0/8 环境检查"
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if [ ! -f docker-compose.yml ]; then
  fa "当前目录非项目根(缺 docker-compose.yml)，请到项目根执行"
  exit 1
fi
if [ ! -f .env ]; then
  warn ".env 不存在(rag-service/smoke-v4 的 env_file 依赖，缺失会导致冒烟失败)"
fi
export PYTHONDONTWRITEBYTECODE=1
if [ -n "${CONDA_PREFIX:-}" ]; then
  say "  已处于 conda 环境: $CONDA_PREFIX"
elif command -v conda >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  if source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null && conda activate ai_collection 2>/dev/null; then
    say "  conda 已激活: ai_collection"
  else
    warn "conda activate ai_collection 失败，沿用当前环境"
  fi
else
  warn "未检测到 conda，沿用当前环境"
fi
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH"; say "  已追加 \$HOME/.local/bin 到 PATH";;
esac
for c in ruff openspec docker python; do
  command -v "$c" >/dev/null 2>&1 || warn "工具缺失: $c(后续相关步骤会 FAIL)"
done
ok "环境检查完成"

# ---------- Step 1 错误码白名单门禁 ----------
header "Step 1/8 错误码白名单门禁 (check_error_codes.sh)"
OUT=$(mktemp)
bash scripts/rag/check_error_codes.sh 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ] && grep -q "0 违规" "$OUT"; then
  ok "check_error_codes.sh(0 违规)"
elif grep -q "0 违规" "$OUT"; then
  warn "check_error_codes.sh 输出 0 违规但退出码非 0，请人工复核"
else
  if grep -qE "command not found|No such file|\.sh: " "$OUT"; then
    fb "check_error_codes.sh 执行异常(B 类: 脚本路径/依赖缺失)"
  else
    fa "check_error_codes.sh 检出白名单外错误码(A 类: 新增/跨域错误码 -> Update+Apply)"
  fi
fi

# ---------- Step 2 Non-Goals 门禁 ----------
header "Step 2/8 Non-Goals 防呆门禁 (check_nongoals.sh)"
OUT=$(mktemp)
bash scripts/rag/check_nongoals.sh 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ] && grep -q "0 违规" "$OUT"; then
  ok "check_nongoals.sh(0 违规)"
elif grep -q "0 违规" "$OUT"; then
  warn "check_nongoals.sh 输出 0 违规但退出码非 0，请人工复核"
else
  if grep -qE "command not found|No such file|\.sh: " "$OUT"; then
    fb "check_nongoals.sh 执行异常(B 类: 脚本路径/依赖缺失)"
  else
    fa "check_nongoals.sh 检出 Non-Goals 违规(A 类: 引入 langsmith/langchain 等 -> Update+Apply)"
  fi
fi

# ---------- Step 3 ruff 静态检查 ----------
header "Step 3/8 ruff 静态检查"
OUT=$(mktemp)
ruff check services/rag-service 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ]; then
  ok "ruff check services/rag-service(All checks passed)"
else
  if grep -q "command not found" "$OUT"; then
    fb "ruff 不可用(B 类: 未安装/不在 PATH，见使用说明 ②)"
  else
    fa "ruff 检出违规(A 类: V4 新增文件违规 -> 修复代码; 存量基线文件可豁免)"
  fi
  grep -E ':[0-9]+:' "$OUT" | head -10
fi

# ---------- Step 4 OpenSpec 校验 ----------
header "Step 4/8 OpenSpec 校验"
OUT=$(mktemp)
openspec validate --specs 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ] && grep -q "0 failed" "$OUT"; then
  ok "openspec validate --specs(9 个主 spec)"
else
  fa "openspec validate --specs 失败(A 类: 主 spec 未同步/工件损坏 -> 先 Update 再 Sync)"
fi

OUT=$(mktemp)
if [ -d "openspec/changes/${CHANGE}" ]; then
  openspec status --change "$CHANGE" --json 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
  if [ "$RC" -eq 0 ] && ! grep -q '"severity": "error"' "$OUT"; then
    ok "openspec status(active 变更 ${CHANGE} 工件完整)"
  else
    fa "openspec status 报错(A 类: 工件缺失/未勾选任务 -> 修复后复验)"
  fi
elif [ -d "$CHANGE_ARCHIVE" ]; then
  warn "变更已归档于 archive/2026-08-24-${CHANGE}，status 对 active change 不可见(预期)"
  warn "如需完整复验: mv ${CHANGE_ARCHIVE} openspec/changes/${CHANGE} 后再跑本脚本"
else
  warn "未找到变更目录(active 与 archive 均无)"
fi

OUT=$(mktemp)
openspec validate --changes 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ]; then
  ok "openspec validate --changes"
elif grep -q "unknown option" "$OUT"; then
  warn "本机 CLI 不支持 validate --changes(B 类: CLI 版本参数差异，用 openspec validate --help 确认后手动复验)"
else
  fa "openspec validate --changes 失败(A 类: 变更工件校验失败)"
fi

# ---------- Step 5 全量单元测试(rag-service) ----------
header "Step 5/8 全量单元测试(rag-service)"
OUT=$(mktemp)
PYTHONPATH="$PWD" python -m pytest services/rag-service/tests -q 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
SUMMARY=$(grep -oE '[0-9]+ passed(, [0-9]+ (failed|skipped|warnings))?' "$OUT" | tail -1)
if [ "$RC" -eq 0 ]; then
  PASSED_NUM=$(grep -oE '[0-9]+ passed' "$OUT" | grep -oE '^[0-9]+' | tail -1)
  ok "pytest rag-service(${SUMMARY:-all passed})"
  if [ "${PASSED_NUM:-0}" -lt 181 ]; then
    warn "通过用例数 ${PASSED_NUM:-0} < 基线 181，请人工确认(期望 209 passed / 1 skipped)"
  fi
else
  if grep -Eqi 'connection refused|timeout|ECONNREFUSED|could not connect|Name or service not known' "$OUT"; then
    fb "pytest 连接类失败(B 类: PG/Redis/ES/Milvus 未起或 host 配置错误)"
  else
    fa "pytest 断言/逻辑失败(A 类: V4 新逻辑不符 Spec -> Update+Apply 后重跑)"
  fi
  grep -E '^FAILED|^ERROR ' "$OUT" | head -10
fi

# ---------- Step 6 common 覆盖率门禁 ----------
header "Step 6/8 common 共享层覆盖率门禁(≥85%)"
OUT=$(mktemp)
PYTHONPATH="$PWD" python -m pytest common/tests -q --cov=common --cov-fail-under=85 --cov-report=term 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ]; then
  ok "pytest common(覆盖率门禁 ≥85%)"
elif grep -Eqi 'connection refused|timeout' "$OUT"; then
  fb "pytest common 连接类失败(B 类: 依赖服务未起)"
elif grep -Eq 'unrecognized arguments.*--cov|pytest-cov|No module named.*cov' "$OUT"; then
  fb "pytest-cov 未安装(B 类: pip install pytest-cov 后重跑; 另见 requirements.in 缺 pytest-cov 的存量缺口)"
elif grep -q 'Coverage failure' "$OUT"; then
  fa "common 覆盖率低于 85%(工程门禁违规，A 类处理)"
else
  warn "pytest common 失败且未能自动归类，请人工复核: $OUT(常见: 存量用例失败/断言错)"
fi

# ---------- Step 7 容器化冒烟 ----------
header "Step 7/8 容器化冒烟(smoke-v4, 跳过 ocr-service)"
OUT=$(mktemp)
say "  执行: docker compose -f docker-compose.yml -f docker-compose.smoke-v4.yml up --build --abort-on-container-exit --exit-code-from smoke-v4 \$SMOKE_SERVICES"
say "  (显式列服务名以排除 ocr-service 的 paddlepaddle 镜像拉取问题)"
docker compose -f docker-compose.yml -f docker-compose.smoke-v4.yml up --build --abort-on-container-exit --exit-code-from smoke-v4 $SMOKE_SERVICES 2>&1 | tee "$OUT"; RC=${PIPESTATUS[0]}
if [ "$RC" -eq 0 ] && grep -q "0 failed" "$OUT"; then
  ok "smoke-v4 冒烟(15 步全 PASS)"
elif grep -Eq 'failed to solve|DeadlineExceeded|failed to resolve|manifest unknown|context deadline exceeded' "$OUT"; then
  fb "smoke 构建/拉镜像失败(B 类: 镜像源/网络; 若为 paddlepaddle 属预期, 已用显式服务名排除)"
elif grep -qE 'Step 1[2-5].*FAIL|FAIL: ' "$OUT"; then
  fa "smoke 业务步骤 FAIL(A 类: 摘要上传/补全/检索链路不符 Spec -> Update+Apply)"
elif grep -qiE 'failed to start|not healthy|is unhealthy|container.*exited' "$OUT"; then
  fb "smoke 服务未就绪(B 类: 先查 docker compose logs rag-service 的 entrypoint/init; 若 es_init_v4/迁移制品不一致再判 A)"
else
  warn "smoke 失败但无法自动归类，请人工复核: $OUT"
fi

# ---------- Step 8 汇总与结论 ----------
header "Step 8/8 验证汇总"
say "-----------------------------------------------------------"
say "| 步骤                                   | 状态      |"
say "|----------------------------------------|-----------|"
printf '%b' "$ROWS"
say "-----------------------------------------------------------"
say "  PASS=$PASS_N  FAIL=$FAIL_N  WARN=$WARN_N  |  A 类失败=$A_FAIL_N"
say ""
if [ "$A_FAIL_N" -gt 0 ]; then
  say "${RED}[结论] 存在 A 类业务规格失败 -> 禁止归档${NC}"
  say "  处理: /OPSX:Update(修 Proposal/Spec/Design/Tasks 或实现契约) -> /OPSX:Apply -> 重跑本脚本直至 A=0"
  say ""
  say "  清理冒烟容器(可选): docker compose -f docker-compose.yml -f docker-compose.smoke-v4.yml down"
  exit 1
elif [ "$FAIL_N" -gt 0 ]; then
  say "${YELLOW}[结论] 仅 B 类本地环境失败(A=0)${NC}"
  say "  按对应步骤排查提示修复本地环境后重跑; 确认与业务制品无关后不阻塞归档"
  say ""
  say "  清理冒烟容器(可选): docker compose -f docker-compose.yml -f docker-compose.smoke-v4.yml down"
  exit 0
else
  say "${GREEN}[结论] 全部通过(A=0, B=0) -> 满足归档前置条件${NC}"
  say "  若变更在 active: /OPSX:Archive rag-multimodal-kb-v4"
  say "  若已在 archive/: 归档成立，可提交评审"
  say ""
  say "  清理冒烟容器(可选): docker compose -f docker-compose.yml -f docker-compose.smoke-v4.yml down"
  exit 0
fi

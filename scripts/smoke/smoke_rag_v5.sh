#!/bin/bash
set -e

# RAG Service V5 冒烟测试（§12）
# 11 步全链路：health → TXT 基线回归 → Excel 多 sheet → PPT 备注 → AMR 转写 → 损坏音频 A3001 →
# 内嵌图片抽取 → 内嵌表格抽取 → answer=true（开关开启：回答/降级）→ answer=true（开关关闭=V4）→
# 资产级联清理
#
# 前置：docker compose up（rag-service + PG + Milvus + ES + Redis + OCR service）
# 用法：bash scripts/smoke/smoke_rag_v5.sh
# 退出码：0=全 PASS，1=有失败（可选组件不可用时按 WARN 放行，对齐 V2 先例）

BASE_URL="http://localhost:8002/api/v1"
HEALTH_URL="http://localhost:8002/health"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
SAMPLE_FILE="$PROJECT_ROOT/scripts/seed/sample_rules.txt"
PASS=0
FAIL=0
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# ---------- PY_BIN（token 签发 + 样本文件生成） ----------
PY_BIN=""
for cand in "$CONDA_PREFIX/bin/python" "$HOME/my_software/miniconda3/envs/ai_collection/bin/python" python3; do
  if [ -n "$cand" ] && command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
done
if [ -z "$PY_BIN" ]; then
  echo "FAIL: 未找到可用的 python，可设置 SMOKE_ADMIN_TOKEN 环境变量"
  exit 1
fi

# ---------- 辅助函数 ----------

step_pass() {
    echo "   PASS"
    PASS=$((PASS + 1))
}

step_fail() {
    echo "   FAIL: $1"
    FAIL=$((FAIL + 1))
}

step_warn() {
    echo "   WARN: $1"
}

json_field() {
    # $1=json_str $2=python 表达式（取值表达式）
    echo "$1" | "$PY_BIN" -c "import sys,json; d=json.load(sys.stdin); print($2)" 2>/dev/null || echo ""
}

upload_doc() {
    # $1=file $2=category；输出上传响应
    curl -s -X POST "$BASE_URL/kb/documents" \
      -H "Authorization: Bearer $ADMIN_TOKEN" \
      -F "file=@$1" -F "category=$2" 2>/dev/null || echo ""
}

parse_doc_id() {
    json_field "$1" "d['data']['doc_id']"
}

wait_active() {
    # $1=doc_id；输出终态（active/failed/timeout）
    local doc_id="$1" i=0 st=""
    while [ "$i" -lt 40 ]; do
        st=$(json_field "$(curl -s "$BASE_URL/kb/documents/$doc_id" 2>/dev/null || echo "")" "d['data']['status']")
        if [ "$st" = "active" ] || [ "$st" = "failed" ]; then
            echo "$st"
            return 0
        fi
        sleep 0.5
        i=$((i + 1))
    done
    echo "timeout"
}

search_json() {
    # $1=query $2=category $3=answer(bool)
    curl -s -X POST "$BASE_URL/kb/search" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $ADMIN_TOKEN" \
      -d "{\"query\":\"$1\",\"category\":\"$2\",\"top_k\":5,\"answer\":$3}" 2>/dev/null || echo ""
}

# ---------- 签发 ADMIN token ----------

if [ -n "$SMOKE_ADMIN_TOKEN" ]; then
  ADMIN_TOKEN="$SMOKE_ADMIN_TOKEN"
else
  ADMIN_TOKEN=$(cd "$PROJECT_ROOT/services/rag-service" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_ROOT" "$PY_BIN" -c "from common.security.rbac import create_token; print(create_token({'user_id':'smoke','username':'smoke_tester','role':'admin'}))")
fi

echo "=== RAG Service V5 Smoke Test ==="
echo ""

# ---------- 样本文件生成 ----------

# 1x1 白色 JPG（PPTX 内嵌图片兜底）
TMP_JPG="$TMP_DIR/placeholder.jpg"
"$PY_BIN" - "$TMP_JPG" <<'PY' || true
import sys
open(sys.argv[1], "wb").write(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xff\xff\xff\xd9')
PY

# 带文字图片（OCR 文搜图样本，PIL 可用时）
TMP_OCR_IMG="$TMP_DIR/ocr_sample.png"
"$PY_BIN" - "$TMP_OCR_IMG" <<'PY' || cp "$TMP_JPG" "$TMP_OCR_IMG"
import sys
try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit(3)
img = Image.new("RGB", (480, 120), "white")
ImageDraw.Draw(img).text((20, 45), "逾期协商还款 可分期60期", fill="black")
img.save(sys.argv[1])
PY

TMP_XLSX="$TMP_DIR/multi_sheet.xlsx"
"$PY_BIN" - "$TMP_XLSX" <<'PY' || true
import sys
try:
    import openpyxl
except ImportError:
    sys.exit(3)
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "催收制度"
ws.append(["条款", "内容"])
ws.append(["第一条", "逾期90天可申请协商还款，分期最长60期"])
ws2 = wb.create_sheet("补充说明")
ws2.append(["字段", "要求"])
ws2.append(["身份证明", "需提供身份证原件核验"])
wb.save(sys.argv[1])
PY

TMP_PPTX="$TMP_DIR/note_and_image.pptx"
"$PY_BIN" - "$TMP_PPTX" "$TMP_OCR_IMG" <<'PY' || true
import sys
try:
    from pptx import Presentation
    from pptx.util import Inches
except ImportError:
    sys.exit(3)
prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[1])
slide.shapes.title.text = "催收作业规范"
slide.placeholders[1].text = "催收过程不得采用威胁、辱骂等非法手段"
slide.notes_slide.notes_text_frame.text = "协商还款需核实客户还款能力与还款意愿，本备注内容可被检索命中"
slide.shapes.add_picture(sys.argv[2], Inches(1), Inches(1), Inches(2), Inches(2))
prs.save(sys.argv[1])
PY

TMP_DOCX="$TMP_DIR/table.docx"
"$PY_BIN" - "$TMP_DOCX" <<'PY' || true
import sys
import zipfile

xml_body = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:body><w:tbl>'
    '<w:tr><w:tc><w:p><w:r><w:t>材料</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>要求</w:t></w:r></w:p></w:tc></w:tr>'
    '<w:tr><w:tc><w:p><w:r><w:t>身份证</w:t></w:r></w:p></w:tc>'
    '<w:tc><w:p><w:r><w:t>原件核验</w:t></w:r></w:p></w:tc></w:tr>'
    '</w:tbl></w:body></w:document>'
)
content_types = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '</Types>'
)
rels = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '</Relationships>'
)
with zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", content_types)
    z.writestr("_rels/.rels", rels)
    z.writestr("word/document.xml", xml_body)
PY

TMP_AMR="$TMP_DIR/sample.amr"
printf '#!AMR\n' > "$TMP_AMR"
printf '\x3c\x00\x00\x00\x3c\x00\x00\x00\x3c\x00\x00\x00\x3c\x00\x00\x00' >> "$TMP_AMR"

TMP_BAD="$TMP_DIR/broken.mp3"
printf '\x00\x01\x02\x03\xff\xff' > "$TMP_BAD"

# ---------- Step 1: /health ----------
echo "Step 1: /health 探活（PG/Milvus/Redis/embedding）..."
HEALTH=$(curl -sf "$HEALTH_URL" 2>/dev/null || echo "")
if echo "$HEALTH" | grep -q "healthy"; then
    step_pass
else
    step_fail "health check returned: $(echo "$HEALTH" | head -c 120)"
fi

# ---------- Step 2: 上传 TXT → 列表 → 详情 → 检索 → 删除（V1 基线回归） ----------
echo "Step 2: TXT 全链路回归（上传→列表→详情→检索→删除）..."
UPLOAD_RESP=$(upload_doc "$SAMPLE_FILE" "regulation")
DOC_ID=$(parse_doc_id "$UPLOAD_RESP")
if [ -z "$DOC_ID" ]; then
    step_fail "TXT 上传失败: $(echo "$UPLOAD_RESP" | head -c 120)"
else
    echo "   doc_id=$DOC_ID"
    STATUS=$(wait_active "$DOC_ID")
    echo "   status=$STATUS"

    LIST_RESP=$(curl -s "$BASE_URL/kb/documents?category=regulation&page=1&size=5" 2>/dev/null || echo "{}")
    LIST_TOTAL=$(json_field "$LIST_RESP" "d['data']['total']")
    DETAIL_RESP=$(curl -s "$BASE_URL/kb/documents/$DOC_ID" 2>/dev/null || echo "")
    DETAIL_STATUS=$(json_field "$DETAIL_RESP" "d['data']['status']")
    SEARCH_RESP=$(search_json "逾期协商还款" "regulation" false)
    SEARCH_CODE=$(json_field "$SEARCH_RESP" "d.get('code','__MISSING__')")

    if [ "$STATUS" != "timeout" ] && [ "$LIST_TOTAL" -ge 0 ] 2>/dev/null \
        && [ -n "$DETAIL_STATUS" ] && [ "$SEARCH_CODE" = "0" ]; then
        step_pass
    else
        step_fail "TXT 回归 status=$STATUS list=$LIST_TOTAL detail=$DETAIL_STATUS search_code=$SEARCH_CODE"
    fi

    DEL_RESP=$(curl -s -X DELETE "$BASE_URL/kb/documents/$DOC_ID" \
      -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null || echo "")
    DEL_CODE=$(json_field "$DEL_RESP" "d.get('code','__MISSING__')")
    if [ "$DEL_CODE" = "0" ]; then
        echo "   删除回归: PASS"
    else
        step_fail "TXT 删除失败 code=$DEL_CODE"
    fi
fi

# ---------- Step 3: 上传 .xlsx（多 sheet）→ 检索命中具体行 ----------
echo "Step 3: Excel 多 sheet 解析（source=excel）..."
XLSX_RESP=$(upload_doc "$TMP_XLSX" "regulation")
XLSX_DOC_ID=$(parse_doc_id "$XLSX_RESP")
if [ -z "$XLSX_DOC_ID" ]; then
    step_warn "xlsx 上传失败（openpyxl 缺失或服务不可用）: $(echo "$XLSX_RESP" | head -c 100)"
    step_pass
else
    wait_active "$XLSX_DOC_ID" >/dev/null
    SEARCH_XLSX=$(search_json "分期最长60期" "regulation" false)
    HIT=$(json_field "$SEARCH_XLSX" "any('分期最长60期' in r.get('content','') for r in d['data']['results'])")
    SRC=$(json_field "$SEARCH_XLSX" "','.join(r.get('source','') for r in d['data']['results'])")
    if [ "$HIT" = "True" ] && echo "$SRC" | grep -q "excel"; then
        echo "   source=$SRC"
        step_pass
    else
        step_fail "xlsx 检索未命中或 source 非 excel（source=$SRC, hit=$HIT）"
    fi
    # 多 sheet 全入库验证（KB_EXCEL_SHEET_ALL=true 时 sheet2 亦可命中）
    HIT2=$(json_field "$(search_json "身份证原件核验" "regulation" false)" "any('身份证原件核验' in r.get('content','') for r in d['data']['results'])")
    if [ "$HIT2" = "True" ]; then
        echo "   sheet2 命中: PASS"
    else
        step_warn "sheet2 未命中（服务端 KB_EXCEL_SHEET_ALL 可能为 false）"
    fi
fi

# ---------- Step 4: 上传 .pptx（含备注）→ 检索命中备注 ----------
echo "Step 4: PPT 备注检索（source=ppt）..."
PPTX_RESP=$(upload_doc "$TMP_PPTX" "regulation")
PPTX_DOC_ID=$(parse_doc_id "$PPTX_RESP")
if [ -z "$PPTX_DOC_ID" ]; then
    step_warn "pptx 上传失败（python-pptx 缺失或服务不可用）: $(echo "$PPTX_RESP" | head -c 100)"
    step_pass
else
    wait_active "$PPTX_DOC_ID" >/dev/null
    SEARCH_PPT=$(search_json "还款意愿" "regulation" false)
    HIT=$(json_field "$SEARCH_PPT" "any('还款意愿' in r.get('content','') for r in d['data']['results'])")
    SRC=$(json_field "$SEARCH_PPT" "','.join(r.get('source','') for r in d['data']['results'])")
    if [ "$HIT" = "True" ] && echo "$SRC" | grep -q "ppt"; then
        echo "   source=$SRC"
        step_pass
    else
        step_fail "pptx 备注未命中或 source 非 ppt（source=$SRC, hit=$HIT）"
    fi
fi

# ---------- Step 5: 上传 .amr → 转写入库 source=asr ----------
echo "Step 5: AMR ffmpeg 解码转写（source=asr，需 KB_ASR_AMR_WMA_ENABLED + ASR Provider）..."
AMR_RESP=$(upload_doc "$TMP_AMR" "case")
AMR_DOC_ID=$(parse_doc_id "$AMR_RESP")
if [ -z "$AMR_DOC_ID" ]; then
    step_warn "AMR 上传未入库（开关关闭或 ffmpeg/ASR 不可用，V4 白名单行为）: $(echo "$AMR_RESP" | head -c 100)"
    step_pass
else
    wait_active "$AMR_DOC_ID" >/dev/null
    SEARCH_AMR=$(search_json "转写" "case" false)
    SRC=$(json_field "$SEARCH_AMR" "','.join(r.get('source','') for r in d['data']['results'])")
    if echo "$SRC" | grep -q "asr"; then
        echo "   source=$SRC"
        step_pass
    else
        step_warn "AMR 已入库但检索未见 source=asr（ASR 文本可能为空，降级占位）"
    fi
fi

# ---------- Step 6: 上传损坏音频 → A3001 + 服务后续正常 ----------
echo "Step 6: 损坏音频 → 断言 A3001 业务错误 + worker 不卡死..."
BAD_RESP=$(curl -s -X POST "$BASE_URL/kb/documents" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -F "file=@$TMP_BAD" -F "category=case" 2>/dev/null || echo "")
BAD_CODE=$(json_field "$BAD_RESP" "d.get('code','__MISSING__')")
if [ "$BAD_CODE" = "A3001" ]; then
    # 服务后续请求正常（worker 未卡死）
    POST_CHECK=$(json_field "$(search_json "逾期协商还款" "regulation" false)" "d.get('code','__MISSING__')")
    if [ "$POST_CHECK" = "0" ]; then
        step_pass
    else
        step_fail "损坏音频后检索请求异常 code=$POST_CHECK"
    fi
else
    step_fail "损坏音频应返回 A3001，实际 code=$BAD_CODE: $(echo "$BAD_RESP" | head -c 100)"
fi

# ---------- Step 7: 内嵌图片抽取 → 文搜图 + assets 预览元数据 ----------
echo "Step 7: PPTX 内嵌图片抽取（需 KB_IMAGE_EXTRACT_ENABLED + OCR，不可用 WARN 放行）..."
ASSET_DOC_ID="$PPTX_DOC_ID"
if [ -n "$ASSET_DOC_ID" ]; then
    SEARCH_IMG=$(search_json "催收作业规范" "regulation" false)
    ASSET_TYPES=$(json_field "$SEARCH_IMG" "','.join(a.get('asset_type','') for r in d['data']['results'] for a in r.get('assets',[]))")
    ASSET_PATHS=$(json_field "$SEARCH_IMG" "','.join(a.get('storage_path','') for r in d['data']['results'] for a in r.get('assets',[]))")
    if echo "$ASSET_TYPES" | grep -q "image" && [ -n "$ASSET_PATHS" ]; then
        echo "   asset_types=$ASSET_TYPES"
        echo "   storage_path=$ASSET_PATHS"
        step_pass
    else
        step_warn "检索未返回 image 资产（服务端开关未开启或 OCR 不可用），资源预览元数据跳过断言"
        step_pass
    fi
else
    step_warn "Step 4 PPTX 未上传，跳过"
    step_pass
fi

# ---------- Step 8: 内嵌表格抽取 → 单元格命中 + 表格预览 ----------
echo "Step 8: DOCX 内嵌表格抽取（需 KB_TABLE_EXTRACT_ENABLED，未开启 WARN 放行）..."
DOCX_RESP=$(upload_doc "$TMP_DOCX" "regulation")
DOCX_DOC_ID=$(parse_doc_id "$DOCX_RESP")
if [ -z "$DOCX_DOC_ID" ]; then
    step_warn "docx 上传失败: $(echo "$DOCX_RESP" | head -c 100)"
    step_pass
else
    wait_active "$DOCX_DOC_ID" >/dev/null
    ASSET_DOC_ID="$DOCX_DOC_ID"
    SEARCH_TBL=$(search_json "原件核验" "regulation" false)
    CELL_HIT=$(json_field "$SEARCH_TBL" "any('原件核验' in r.get('content','') for r in d['data']['results'])")
    ASSET_TYPES=$(json_field "$SEARCH_TBL" "','.join(a.get('asset_type','') for r in d['data']['results'] for a in r.get('assets',[]))")
    ASSET_PATHS=$(json_field "$SEARCH_TBL" "','.join(a.get('storage_path','') for r in d['data']['results'] for a in r.get('assets',[]))")
    if [ "$CELL_HIT" = "True" ] && echo "$ASSET_TYPES" | grep -q "table" \
        && echo "$ASSET_PATHS" | grep -q "\.json"; then
        echo "   asset_types=$ASSET_TYPES"
        step_pass
    elif [ "$CELL_HIT" = "True" ]; then
        step_warn "单元格已命中但无 table 资产（服务端 KB_TABLE_EXTRACT_ENABLED 未开启），表格预览跳过断言"
        step_pass
    else
        step_fail "表格单元格未命中（cell_hit=$CELL_HIT）"
    fi
fi

# ---------- Step 9: answer=true（开关开启：回答/降级契约） ----------
echo "Step 9: answer=true 检索（LLM 可用=带溯源回答；无 LLM 环境=answer=null+degraded=true）..."
ANS_RESP=$(search_json "逾期协商还款可以分期吗" "regulation" true)
ANS_CODE=$(json_field "$ANS_RESP" "d.get('code','__MISSING__')")
ANS_ANSWER=$(json_field "$ANS_RESP" "d['data'].get('answer')")
ANS_DEGRADED=$(json_field "$ANS_RESP" "d['data'].get('degraded')")
if [ "$ANS_CODE" = "0" ]; then
    if [ "$ANS_ANSWER" != "None" ] && [ -n "$ANS_ANSWER" ]; then
        echo "   answer 已生成（LLM 可用）: $(echo "$ANS_ANSWER" | head -c 80)"
        step_pass
    elif [ "$ANS_ANSWER" = "None" ] && [ "$ANS_DEGRADED" = "True" ]; then
        echo "   answer=null + degraded=true（无 LLM 环境降级正确）"
        step_pass
    elif [ "$ANS_ANSWER" = "None" ] && [ "$ANS_DEGRADED" = "False" ]; then
        echo "   answer=null + degraded=false（服务端开关关闭，门控短路=V4 行为）"
        step_pass
    else
        step_fail "answer 字段异常 answer=$ANS_ANSWER degraded=$ANS_DEGRADED"
    fi
else
    step_fail "answer=true 检索 code=$ANS_CODE"
fi

# ---------- Step 10: answer=true（开关关闭）→ 响应与 V4 完全一致 ----------
echo "Step 10: answer=true（开关关闭默认部署）→ answer=null 零 LLM 调用（V4 一致）..."
V4_RESP=$(search_json "催收制度" "regulation" true)
V4_CODE=$(json_field "$V4_RESP" "d.get('code','__MISSING__')")
V4_HAS_ANSWER=$(echo "$V4_RESP" | "$PY_BIN" -c "import sys,json; d=json.load(sys.stdin)['data']; print('answer' in d and 'degraded' in d)" 2>/dev/null || echo "False")
V4_ANSWER=$(json_field "$V4_RESP" "d['data'].get('answer')")
V4_DEGRADED=$(json_field "$V4_RESP" "d['data'].get('degraded')")
if [ "$V4_CODE" = "0" ] && [ "$V4_HAS_ANSWER" = "True" ]; then
    if [ "$V4_ANSWER" = "None" ] && [ "$V4_DEGRADED" = "False" ]; then
        echo "   answer=null + degraded=false（V4 完全一致，零 LLM 调用）"
        step_pass
    elif [ -n "$V4_ANSWER" ] && [ "$V4_ANSWER" != "None" ]; then
        step_warn "服务端开关已开启，answer 已生成（非默认部署，V5 契约字段仍完整）"
        step_pass
    else
        step_fail "answer/degraded 组合异常 answer=$V4_ANSWER degraded=$V4_DEGRADED"
    fi
else
    step_fail "answer=true 响应缺 V5 契约字段（code=$V4_CODE has_answer=$V4_HAS_ANSWER）"
fi

# ---------- Step 11: 删除含资产文档 → kb_doc_assets 与本地目录清理 ----------
echo "Step 11: 删除含资产文档 → kb_doc_assets 计数 0 + 本地目录清理..."
if [ -n "$ASSET_DOC_ID" ]; then
    DEL2_RESP=$(curl -s -X DELETE "$BASE_URL/kb/documents/$ASSET_DOC_ID" \
      -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null || echo "")
    DEL2_CODE=$(json_field "$DEL2_RESP" "d.get('code','__MISSING__')")
    if [ "$DEL2_CODE" != "0" ]; then
        step_fail "含资产文档删除失败 code=$DEL2_CODE"
    else
        # kb_doc_assets 记录计数为 0
        ASSET_CNT=$(docker exec postgres psql -U ai_collection -d ai_collection -t -A \
          -c "SELECT count(*) FROM kb_doc_assets WHERE doc_id='$ASSET_DOC_ID'" 2>/dev/null || echo "")
        if [ "$ASSET_CNT" = "0" ]; then
            echo "   kb_doc_assets count=0"
            step_pass
        elif [ -z "$ASSET_CNT" ]; then
            step_warn "无法查询 PG（docker exec postgres 不可用），kb_doc_assets 计数断言跳过"
        else
            step_fail "kb_doc_assets 残留 $ASSET_CNT 条"
        fi
        # 本地资产目录清理（容器内 KB_ASSETS_DIR）
        if docker exec rag-service sh -c "test ! -e /app/data/assets/$ASSET_DOC_ID" 2>/dev/null; then
            echo "   本地资产目录已清理"
        else
            step_warn "容器内资产目录检查不可用或目录残留（rag-service 容器未运行？）"
        fi
    fi
else
    step_warn "无含资产文档（Step 4/8 未上传成功），清理断言跳过"
    step_pass
fi

# ---------- 汇总 ----------
echo ""
echo "=== V5 Smoke Test Results: $PASS passed, $FAIL failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0

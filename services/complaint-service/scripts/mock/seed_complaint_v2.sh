#!/usr/bin/env bash
# 将 V2 种子投诉灌入本地 complaint-service（默认 8006）。
# 用法：
#   bash services/complaint-service/scripts/mock/seed_complaint_v2.sh
#   BASE_URL=http://localhost:8006 bash services/complaint-service/scripts/mock/seed_complaint_v2.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${SERVICE_ROOT}/../.." && pwd)"
BASE_URL="${BASE_URL:-http://localhost:8006}"
export SEED_API="${BASE_URL}/api/v1"
export PYTHONPATH="${SERVICE_ROOT}:${REPO_ROOT}:${PYTHONPATH:-}"

echo "[seed-v2] health check ${BASE_URL}/health"
curl -sf "${BASE_URL}/health" >/dev/null
echo "[seed-v2] ok → POST ${SEED_API}/complaints"

python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

from tests.mock.data.seed_complaints_v2 import V2_SEED_COMPLAINTS

api = os.environ["SEED_API"]
created = []
for seed in V2_SEED_COMPLAINTS:
    payload = {
        "complaint_text": seed["complaint_text"],
        "case_id": seed["case_id"],
        "channel": seed.get("channel", "phone"),
    }
    req = urllib.request.Request(
        f"{api}/complaints",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"[seed-v2] FAIL {seed['case_id']}: HTTP {e.code} {err}", file=sys.stderr)
        sys.exit(1)
    data = body.get("data") or body
    cid = data.get("complaint_id") or seed["case_id"]
    created.append(cid)
    print(f"[seed-v2] created complaint_id={cid} scenario={seed['scenario']}")

print(f"[seed-v2] done count={len(created)} ids={created}")
PY

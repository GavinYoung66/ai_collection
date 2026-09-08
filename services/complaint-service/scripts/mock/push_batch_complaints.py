#!/usr/bin/env python3
"""批量模拟投诉工单推送脚本。

批次0 边界：
  - 本脚本仅验证可运行形态（--help / 参数解析 / 读取种子）。
  - 实际 HTTP 推送与 GET status 联调属【批次7】，本批不要求 API 已通。

用法：
  python scripts/mock/push_batch_complaints.py --count 5 --delay 1 --mode normal
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# 确保可从服务根导入 tests.mock.data
SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from tests.mock.data.seed_complaints import SEED_COMPLAINTS  # noqa: E402

DEFAULT_BASE_URL = "http://localhost:8006/api/v1"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="批量模拟投诉工单推送（批次0 mock，联调属批次7）")
    p.add_argument("--count", type=int, default=3, help="推送工单数（默认 3）")
    p.add_argument("--delay", type=float, default=0.5, help="每条推送间隔秒数（默认 0.5）")
    p.add_argument(
        "--mode",
        choices=["normal", "burst", "error"],
        default="normal",
        help="normal: 顺序推送; burst: 无间隔高并发; error: 模拟异常工单",
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="complaint-service base URL")
    p.add_argument("--dry-run", action="store_true", help="仅打印不实际发送（默认行为，因 API 未就绪）")
    return p


def main() -> int:
    args = build_parser().parse_args()

    print(f"[mock-push] mode={args.mode} count={args.count} delay={args.delay}s dry_run={args.dry_run}")
    print(f"[mock-push] base_url={args.base_url}")
    print("[mock-push] 注意：实际 HTTP 推送与 GET status 联调属批次7，本批仅验证脚本形态。")

    if args.count <= 0:
        print("[mock-push] count 必须 > 0")
        return 1

    for i in range(args.count):
        complaint = SEED_COMPLAINTS[i % len(SEED_COMPLAINTS)]
        payload = {
            "complaint_id": complaint["complaint_id"],
            "user_id": complaint["user_id"],
            "case_ids": complaint["case_ids"],
            "complaint_text": complaint["complaint_text"],
            "submit_time": complaint["submit_time"],
            "source_channel": complaint["source_channel"],
        }
        print(f"[mock-push] [{i+1}/{args.count}] POST /complaints  {json.dumps(payload, ensure_ascii=False)[:120]}...")

        if not args.dry_run:
            # 批次7：实际调用 requests.post(args.base_url + "/complaints", json=payload)
            print("[mock-push]   (dry-run 跳过 HTTP 调用，批次7联调)")

        if args.mode != "burst":
            time.sleep(args.delay)

    print(f"[mock-push] 完成：{args.count} 条模拟推送（dry-run）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

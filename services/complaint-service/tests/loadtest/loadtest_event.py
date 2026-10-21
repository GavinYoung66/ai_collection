#!/usr/bin/env python3
"""loadtest_event.py — 投诉研判「事件入口」洪峰/长稳压测脚本（批次5 专用）

与现有只打 HTTP 入口的 locustfile.py 不同，本脚本一次性向 RocketMQ 发布
``complaint.created``（payload 仅白名单 case_id），由 complaint-service 真实
订阅消费（common.eventbus → subscriber.handle_complaint_created → judge 跑图）。
脚本只负责「发事件 + 采指标 + 对账面」，不做任何研判断言；研判质量靠后续 PG 对账。

发布实现复用 data-platform 官方 publisher：
services/data-platform/app/events/publisher.py:publish_complaint_created
（与 services/data-platform/scripts/run_batch_flow.py:197-226 同口径）。

本文件是批次5 唯一允许新建的文件；禁止引用/修改 app/ 与 common/ 业务代码。

子命令：
  publish      一次性（或一次性批次）发布 N 条 complaint.created
  watch        轮询 PG 对账：终态数/在途数/丢失数/重复数 + 端到端 P50/P95/P99 + 降级率
  soak         长稳压测：周期性补发，保持 worker 满载 N 分钟（5.3）
  sample       资源采样：RSS/TCP/PG 连接/Redis key（5.4），输出 CSV
  logmetrics   从 docker logs 采 LLM 429/熔断、MCP 超时/限流事件计数（5.1 指标①③）

典型在宿主机执行（仓库根目录，端口 18081/5432/6379 均已映射）：
  python3 services/complaint-service/tests/loadtest/loadtest_event.py publish \
      --tier 4 --count 100 --start-index 0
  python3 services/complaint-service/tests/loadtest/loadtest_event.py watch \
      --artifact <publish 产物 jsonl> --timeout 2400
  python3 services/complaint-service/tests/loadtest/loadtest_event.py soak \
      --duration 1800 --inflight-target 32 --interval 20
  python3 services/complaint-service/tests/loadtest/loadtest_event.py sample \
      --interval 300 --duration 1800
  python3 services/complaint-service/tests/loadtest/loadtest_event.py logmetrics \
      --since-rfc3339 2026-10-02T09:10:00Z --until-rfc3339 2026-10-02T09:40:00Z

说明：
- 消费并行度（ROCKETMQ_CONSUMER_PARALLELISM）是 complaint-service **消费端**
  环境变量，需在 .env 改后 recreate 容器生效；本脚本只读取并记录该值用于报告标注，
  不负责切换。
- 业务键幂等：complaint_id == case_id，已研判/在途 case 重发会被订阅器
  idempotent_skip 不跑图，因此每档必须使用全新 qc_ready case，脚本默认 SQL 已排除
  已投诉 case，并额外排除历史发布产物中用过的 case_id。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- 路径引导（与 run_batch_flow.py 同法）-----------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent                      # tests/loadtest
_CS_ROOT = _SCRIPT_DIR.parents[1]                                  # services/complaint-service
_SERVICES_ROOT = _SCRIPT_DIR.parents[2]                            # services
_REPO_ROOT = _SCRIPT_DIR.parents[3]                                # 仓库根
_DP_ROOT = _SERVICES_ROOT / "data-platform"
for _p in (_REPO_ROOT, _CS_ROOT, _DP_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# 运行产物统一进既有 reports 树（不新增源码文件）
REPORT_DIR = _CS_ROOT / "scripts" / "loadtest" / "reports" / "event"

# 终态：人审类 + 已完成类（与 task_status 11 值对齐；pending/evidence_gathering/
# reflection_retry/judging/draft_generated 均为在途）
TERMINAL_STATUSES = (
    "pending_review",
    "pending_senior_review",
    "pending_callback",
    "resolved",
    "closed",
    "archived",
)
# 降级口径（用户验收定义）：task_status=pending_review 且 exception_info 非空
DEGRADE_STATUS = "pending_review"

# 订阅器业务键跳过集（必须与
# services/complaint-service/app/events/subscriber.py:_SKIP_JUDGE_STATUSES 逐字一致）：
# 仅「pending 仅建单」状态重发事件会复用旧单重新跑图；下列状态直接 idempotent_skip
SKIP_JUDGE_STATUSES = (
    "pending_review",
    "pending_senior_review",
    "pending_callback",
    "judging",
    "draft_generated",
    "evidence_gathering",
    "reflection_retry",
    "resolved",
    "closed",
    "archived",
)

# 投诉服务与基础设施容器名（dev compose 栈）
COMPLAINT_CONTAINER = os.getenv("LT_COMPLAINT_CONTAINER", "ai_collection-complaint-service-1")
PG_CONTAINER = os.getenv("LT_PG_CONTAINER", "ai_collection-postgres-1")
REDIS_CONTAINER = os.getenv("LT_REDIS_CONTAINER", "ai_collection-redis-1")
GATEWAY_CONTAINER = os.getenv("LT_GATEWAY_CONTAINER", "ai_collection-mcp-gateway-1")
REDIS_DB = os.getenv("LT_REDIS_DB", "6")

# 指标①③日志关键字（均为结构化日志里稳定出现的事件名/异常类名/错误文案）
LOG_PATTERNS: dict[str, re.Pattern] = {
    # ① LLM 429 / 限流 / 熔断
    "llm_rate_limited": re.compile(r"LLMRateLimitError|LLM rate limit exceeded", re.I),
    "llm_circuit_open": re.compile(r"llm_circuit_open", re.I),
    "llm_chain_degraded": re.compile(r"llm_chain_degraded", re.I),
    # ③ MCP 超时 / 限流 / 鉴权（投诉侧 + 网关侧）
    # 限流/鉴权只匹配结构化事件名与网关 JSON-RPC 错误文本，禁止裸数字 401/403/429
    # （uuid/trace_id 中大量包含这些数字子串，run9 实测 107/10 条全为误报）
    "mcp_timeout": re.compile(r"MCPTimeoutError|gateway timeout|mcp.*timeout", re.I),
    "mcp_rate_limited": re.compile(r"mcp_rate_limit|rate_limited|too many requests", re.I),
    "mcp_permission_denied": re.compile(r"mcp_permission_denied|MCPPermissionDeniedError", re.I),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_repo_env() -> None:
    """只读加载仓库根 .env 进环境（setdefault，不覆盖已显式导出的变量）。

    根 .env 的 ROCKETMQ_PROXY 是容器内 DNS（rocketmq-proxy:8081），宿主机执行
    压测脚本时不可解析，默认改写为 localhost:18081（见 _bootstrap_bus_env）。
    """
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _bootstrap_bus_env(rocketmq_proxy: str | None) -> None:
    """压测脚本宿主机执行所需的事件总线环境（发布端身份=dp，与批量流水一致）。"""
    os.environ["EVENT_BUS"] = "rocketmq"
    os.environ.setdefault("EVENT_BUS_SVC", "dp")
    os.environ["ROCKETMQ_PROXY"] = rocketmq_proxy or "localhost:18081"
    # 发布端不需要 namesrv，但客户端工厂可能初始化时读取；给宿主机映射兜底
    os.environ.setdefault("ROCKETMQ_NAMESRV", "localhost:19876")
    from common.eventbus.factory import reset_event_bus

    reset_event_bus()


def _pg_engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from common.config.settings import settings

    return create_async_engine(settings.pg_dsn, poolclass=NullPool)


def _all_artifact_case_ids() -> set[str]:
    """汇总 reports/event 下历史发布产物已使用的 case_id，防跨轮复用。"""
    used: set[str] = set()
    if not REPORT_DIR.exists():
        return used
    for path in REPORT_DIR.glob("publish_*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    cid = rec.get("case_id")
                    if cid:
                        used.add(str(cid))
        except (OSError, json.JSONDecodeError):
            continue
    return used


async def _fetch_fresh_case_pool(extra_exclude: set[str] | None = None) -> list[str]:
    """取可跑图案池：qc_ready 且不存在「跳过状态」会话（与订阅器幂等语义同口径）。

    历史遗留的 pending「仅建单」complaint（无 session）会被订阅器复用重新跑图，
    属于可用案；一旦本批次某档产出终态会话，该案自动移出池。
    """
    engine = _pg_engine()
    try:
        from sqlalchemy import text

        sql = text(
            """
            SELECT a.case_id::text AS case_id
            FROM dw.ads_case_loop a
            WHERE a.qc_record_id IS NOT NULL
              AND a.compliance_score IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM complaints c
                  JOIN complaint_sessions s ON s.complaint_id = c.id
                  WHERE c.case_id::text = a.case_id
                    AND s.is_delete = '0'
                    AND s.task_status = ANY(:skip)
              )
            ORDER BY a.case_id
            """
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(sql, {"skip": list(SKIP_JUDGE_STATUSES)})).all()
    finally:
        await engine.dispose()
    pool = [r[0] for r in rows]
    exclude = _all_artifact_case_ids()
    if extra_exclude:
        exclude |= extra_exclude
    return [cid for cid in pool if cid not in exclude]


def _read_case_id_file(path: str) -> list[str]:
    cids: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#"):
                cids.append(line.split(",")[0].strip())
    return cids


async def _publish_batch(case_ids: list[str], tier: str, run_tag: str, trace_prefix: str) -> list[dict]:
    """逐条发布（一次性快速连发）；失败不中断批次，返回逐条发布记录。"""
    from app.events.publisher import publish_complaint_created

    records: list[dict] = []
    total = len(case_ids)
    for idx, cid in enumerate(case_ids, 1):
        trace_id = f"{trace_prefix}-{run_tag}-{cid}"
        ts = time.time()
        try:
            ok = await publish_complaint_created(case_id=cid, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001 — 压测脚本不能因单条异常中断
            ok = False
            err = f"{type(exc).__name__}: {exc}"
        else:
            err = ""
        records.append(
            {
                "run_tag": run_tag,
                "tier": tier,
                "parallelism_env": os.getenv("ROCKETMQ_CONSUMER_PARALLELISM", "1(默认)"),
                "case_id": cid,
                "trace_id": trace_id,
                "publish_ts": ts,
                "publish_iso": datetime.fromtimestamp(ts, timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
                "publish_ok": bool(ok),
                "error": err,
                "seq": idx,
                "total": total,
            }
        )
        if idx % 10 == 0 or idx == total:
            print(f"  发布进度 {idx}/{total} 成功={sum(1 for r in records if r['publish_ok'])}", end="\r")
    print()
    return records


def _write_publish_artifact(records: list[dict]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_tag = records[0]["run_tag"] if records else "empty"
    path = REPORT_DIR / f"publish_{run_tag}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def _read_artifact(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


async def cmd_publish(args: argparse.Namespace) -> int:
    _load_repo_env()
    _bootstrap_bus_env(args.rocketmq_proxy)

    if args.case_id_file:
        pool = _read_case_id_file(args.case_id_file)
    else:
        pool = await _fetch_fresh_case_pool()
    if args.start_index >= len(pool):
        print(f"[FAIL] start-index={args.start_index} 超出案池容量 {len(pool)}")
        return 2
    selected = pool[args.start_index : args.start_index + args.count]
    if len(selected) < args.count:
        print(
            f"[FAIL] 案池不足：需要 {args.count} 条，剩余 {len(selected)} 条"
            f"（池总量 {len(pool)}，start-index={args.start_index}）。请先造数或调整起始位置。"
        )
        return 2

    run_tag = args.run_tag or f"{args.tier or 'na'}-{int(time.time())}"
    trace_prefix = args.trace_prefix or "lt5"
    print(
        f"[INFO] tier={args.tier} 条数={len(selected)} "
        f"ROCKETMQ_PROXY={os.environ['ROCKETMQ_PROXY']} run_tag={run_tag}"
    )
    if args.dry_run:
        print("[DRY-RUN] 选中 case_id：")
        for cid in selected:
            print(" ", cid)
        return 0

    t0 = time.time()
    records = await _publish_batch(selected, args.tier or "na", run_tag, trace_prefix)
    path = _write_publish_artifact(records)
    ok = sum(1 for r in records if r["publish_ok"])
    failed = len(records) - ok
    print(f"[DONE] 发布成功={ok} 失败={failed} 耗时={time.time() - t0:.1f}s")
    print(f"[ARTIFACT] {path}")
    return 0 if failed == 0 else 1


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


async def _query_case_outcomes(case_ids: list[str]) -> dict[str, dict]:
    """按 case_id 聚合研判结果（complaint → complaint_sessions + checkpoint 线程）。

    返回 {case_id: {sessions: n, complaints: n, status, exception_nonempty, terminal_ts}}
    """
    engine = _pg_engine()
    try:
        from sqlalchemy import text

        sql = text(
            """
            SELECT c.case_id::text AS case_id,
                   COUNT(DISTINCT c.id) AS complaint_rows,
                   COUNT(s.session_id) AS session_rows,
                   COALESCE(MAX(s.task_status), '') AS task_status,
                   BOOL_OR(s.exception_info IS NOT NULL
                           AND s.exception_info <> '{}'::jsonb
                           AND s.exception_info <> 'null'::jsonb) AS has_exception,
                   COALESCE(MAX(s.update_time), NULL) AS last_update,
                   COUNT(DISTINCT s.session_id) FILTER (
                       WHERE s.task_status = ANY(:terminal)
                   ) AS terminal_sessions
            FROM complaints c
            LEFT JOIN complaint_sessions s ON s.complaint_id = c.id AND s.is_delete = '0'
            WHERE c.case_id::text = ANY(:cids)
            GROUP BY c.case_id
            """
        )
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    sql,
                    {"cids": case_ids, "terminal": list(TERMINAL_STATUSES)},
                )
            ).mappings().all()
    finally:
        await engine.dispose()
    out: dict[str, dict] = {}
    for r in rows:
        out[str(r["case_id"])] = {
            "complaint_rows": r["complaint_rows"],
            "session_rows": r["session_rows"],
            "task_status": r["task_status"] or "",
            "has_exception": bool(r["has_exception"]),
            "terminal_ts": r["last_update"].timestamp() if r["last_update"] else None,
            "terminal_sessions": r["terminal_sessions"],
        }
    return out


async def cmd_watch(args: argparse.Namespace) -> int:
    _load_repo_env()
    records = _read_artifact(args.artifact)
    published = [r for r in records if r.get("publish_ok")]
    case_ids = [r["case_id"] for r in published]
    publish_ts = {r["case_id"]: r["publish_ts"] for r in published}
    total = len(case_ids)
    print(f"[INFO] watch 产物={args.artifact} 发布成功={total} timeout={args.timeout}s")

    deadline = time.time() + args.timeout
    result: dict | None = None
    last_status_dist: dict[str, int] = {}
    while time.time() < deadline:
        outcomes = await _query_case_outcomes(case_ids)
        terminal = [cid for cid, o in outcomes.items() if o["task_status"] in TERMINAL_STATUSES]
        inflight = [
            cid
            for cid, o in outcomes.items()
            if o["task_status"] and o["task_status"] not in TERMINAL_STATUSES
        ]
        missing = [cid for cid in case_ids if cid not in outcomes]
        last_status_dist = {}
        for o in outcomes.values():
            last_status_dist[o["task_status"] or "(无session)"] = (
                last_status_dist.get(o["task_status"] or "(无session)", 0) + 1
            )
        print(
            f"  {_utc_now_iso()} 终态={len(terminal)}/{total} "
            f"在途={len(inflight)} 未落库={len(missing)} 状态分布={last_status_dist}",
            flush=True,
        )
        if len(terminal) >= total:
            break
        await asyncio.sleep(args.interval)

    outcomes = await _query_case_outcomes(case_ids)
    terminal_cases, inflight_cases, missing_cases = [], [], []
    latencies: list[float] = []
    degraded = 0
    dup_complaint_cases = []
    dup_session_cases = []
    status_dist: dict[str, int] = {}
    degrade_with_exc = 0
    for cid in case_ids:
        o = outcomes.get(cid)
        if o is None:
            missing_cases.append(cid)
            continue
        st = o["task_status"]
        status_dist[st or "(无session)"] = status_dist.get(st or "(无session)", 0) + 1
        if o["complaint_rows"] > 1:
            dup_complaint_cases.append(cid)
        if o["session_rows"] > 1:
            dup_session_cases.append(cid)
        if st in TERMINAL_STATUSES:
            terminal_cases.append(cid)
            if o["terminal_ts"]:
                latencies.append(o["terminal_ts"] - publish_ts[cid])
            if st == DEGRADE_STATUS:
                degraded += 1
                if o["has_exception"]:
                    degrade_with_exc += 1
        elif st:
            inflight_cases.append(cid)
        else:
            missing_cases.append(cid)

    latencies.sort()
    wall_clock = (max(publish_ts.values()) - min(publish_ts.values())) if publish_ts else 0.0
    result = {
        "artifact": args.artifact,
        "generated_at": _utc_now_iso(),
        "published_ok": total,
        "terminal": len(terminal_cases),
        "inflight": len(inflight_cases),
        "missing": len(missing_cases),
        "duplicate_complaint_cases": len(dup_complaint_cases),
        "duplicate_session_cases": len(dup_session_cases),
        "degrade_pending_review": degraded,
        "degrade_with_exception": degrade_with_exc,
        "degrade_rate": round(degrade_with_exc / total, 4) if total else None,
        "latency_s": {
            "p50": round(_percentile(latencies, 0.50) or 0.0, 2),
            "p95": round(_percentile(latencies, 0.95) or 0.0, 2),
            "p99": round(_percentile(latencies, 0.99) or 0.0, 2),
            "max": round(latencies[-1], 2) if latencies else None,
            "min": round(latencies[0], 2) if latencies else None,
            "samples": len(latencies),
        },
        "publish_wall_clock_s": round(wall_clock, 2),
        "status_distribution": status_dist,
        "inflight_case_ids": inflight_cases[:50],
        "missing_case_ids": missing_cases[:50],
        "dup_complaint_case_ids": dup_complaint_cases[:50],
        "dup_session_case_ids": dup_session_cases[:50],
    }
    out_path = Path(args.out) if args.out else REPORT_DIR / f"watch_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 对账结果 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[ARTIFACT] {out_path}")
    # 非全量终态以退出码 1 提示（便于编排判断超时）
    return 0 if result["terminal"] == total else 1


async def cmd_soak(args: argparse.Namespace) -> int:
    """5.3 长稳：周期性补发把在途量维持在 inflight-target，直到 duration 结束。"""
    _load_repo_env()
    _bootstrap_bus_env(args.rocketmq_proxy)
    run_tag = args.run_tag or f"soak-{int(time.time())}"
    trace_prefix = args.trace_prefix or "lt5"

    # 预取足量案池（在途目标 × 补发轮数 +余量），一次性锁定避免中途缺案
    rounds = int(args.duration / args.interval) + 2
    need = args.inflight_target + args.burst * rounds
    pool = await _fetch_fresh_case_pool()
    if len(pool) < need:
        print(f"[FAIL] 长稳案池不足：需约 {need}，可用 {len(pool)}（请先造数）")
        return 2
    cursor = args.start_index
    all_records: list[dict] = []
    deadline = time.time() + args.duration
    round_no = 0
    print(
        f"[INFO] soak run_tag={run_tag} duration={args.duration}s "
        f"target={args.inflight_target} interval={args.interval}s burst={args.burst}"
    )
    while time.time() < deadline:
        round_no += 1
        # 当前在途：本 run 已发布且未终态（直接查 PG，口径与 watch 一致）
        published_ids = [r["case_id"] for r in all_records if r["publish_ok"]]
        inflight_now = 0
        if published_ids:
            outcomes = await _query_case_outcomes(published_ids)
            inflight_now = sum(
                1
                for o in outcomes.values()
                if o["task_status"] and o["task_status"] not in TERMINAL_STATUSES
            )
        topup = max(0, args.inflight_target - inflight_now)
        topup = min(topup, args.burst, len(pool) - cursor)
        if topup > 0:
            selected = pool[cursor : cursor + topup]
            cursor += topup
            recs = await _publish_batch(selected, args.tier or "final", run_tag, trace_prefix)
            all_records.extend(recs)
        print(
            f"  soak round={round_no} 在途≈{inflight_now} 补发={topup} "
            f"累计发布={len(all_records)} 剩余案池={len(pool) - cursor}",
            flush=True,
        )
        if topup <= 0 and round_no == 1:
            print("[WARN] 首轮即无需补发，target 可能偏低，worker 未必满载")
        await asyncio.sleep(min(args.interval, max(0.0, deadline - time.time())))

    path = _write_publish_artifact(all_records)
    ok = sum(1 for r in all_records if r["publish_ok"])
    print(f"[DONE] soak 结束 发布成功={ok}/{len(all_records)} [ARTIFACT] {path}")
    return 0 if ok == len(all_records) else 1


# --- sample：资源采样 --------------------------------------------------------
def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:  # pragma: no cover
        return ""
    except FileNotFoundError:  # pragma: no cover
        return ""


def _docker_ips() -> dict[str, str]:
    """取基础设施容器在 compose 网络的 IPv4（用于 /proc/net/tcp 对端归类）。"""
    targets = {
        "postgres": PG_CONTAINER,
        "redis": REDIS_CONTAINER,
        "mcp_gateway": GATEWAY_CONTAINER,
        "rocketmq_proxy": "ai_collection-rocketmq-proxy-1",
        "rocketmq_broker": "ai_collection-rocketmq-broker-1",
    }
    ips: dict[str, str] = {}
    for label, name in targets.items():
        out = _run(["docker", "inspect", name, "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"])
        for ip in out.split():
            if ip and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                ips[ip] = label
    return ips


def _parse_hex_addr(raw: str) -> tuple[str, int]:
    """/proc/net/tcp(6) 地址字段：小端十六进制 IP + 4 位十六进制端口。

    tcp6 表中 IPv4-mapped 地址形如 00000000000000000000FFFFAC140011，
    取末尾 8 位按 IPv4 小端解析；其余 IPv6 地址无法与 IPv4 对端表映射，跳过。
    """
    ip_hex, port_hex = raw.split(":")
    port = int(port_hex, 16)
    mapped_prefix = "0000000000000000ffff0000"
    if len(ip_hex) == 32 and ip_hex.lower().startswith(mapped_prefix):
        ip_hex = ip_hex[-8:]
    if len(ip_hex) != 8:
        raise ValueError("non-ipv4 address")
    b = bytes.fromhex(ip_hex)
    ip = ".".join(str(x) for x in reversed(b))
    return ip, port


def _tcp_counts(peer_ips: dict[str, str]) -> dict[str, int]:
    counts = {"total_established": 0, "pg": 0, "redis": 0, "mcp_gateway": 0, "rocketmq": 0}
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        out = _run(["docker", "exec", COMPLAINT_CONTAINER, "cat", table])
        for line in out.splitlines()[1:]:
            cols = line.split()
            if len(cols) < 4 or cols[3] != "01":  # 01=ESTABLISHED
                continue
            counts["total_established"] += 1
            try:
                ip, port = _parse_hex_addr(cols[2])
            except (ValueError, IndexError):
                continue
            label = peer_ips.get(ip)
            if label == "postgres" and port == 5432:
                counts["pg"] += 1
            elif label == "redis" and port == 6379:
                counts["redis"] += 1
            elif label == "mcp_gateway" and port == 9000:
                counts["mcp_gateway"] += 1
            elif label in ("rocketmq_proxy", "rocketmq_broker"):
                counts["rocketmq"] += 1
    return counts


def _sample_once(peer_ips: dict[str, str]) -> dict:
    row: dict[str, float | str | int] = {"ts": _utc_now_iso()}
    # RSS
    stats = _run(
        [
            "docker",
            "stats",
            COMPLAINT_CONTAINER,
            "--no-stream",
            "--format",
            "{{.MemUsage}}|{{.CPUPerc}}",
        ]
    ).strip()
    mem = stats.split("|")[0].strip() if stats else ""
    m = re.match(r"([\d.]+)([A-Za-z]+)", mem)
    rss_mib: float | None = None
    if m:
        val, unit = float(m.group(1)), m.group(2).lower()
        rss_mib = {
            "kib": val / 1024,
            "mib": val,
            "gib": val * 1024,
            "b": val / (1024 * 1024),
            "kb": val / 1024,
            "mb": val,
            "gb": val * 1024,
        }.get(unit)
    row["rss_mib"] = round(rss_mib, 1) if rss_mib is not None else ""
    row.update(_tcp_counts(peer_ips))
    # PG 连接：状态/等待事件分布（ai_collection 库）
    pg_out = _run(
        [
            "docker",
            "exec",
            PG_CONTAINER,
            "psql",
            "-U",
            "ai_collection",
            "-d",
            "ai_collection",
            "-t",
            "-A",
            "-F",
            ":",
            "-c",
            "SELECT state || '=' || cnt FROM ("
            "SELECT COALESCE(state,'null') AS state, COUNT(*) AS cnt "
            "FROM pg_stat_activity WHERE datname='ai_collection' GROUP BY 1) t ORDER BY 1;",
        ]
    )
    states: dict[str, int] = {}
    for line in pg_out.splitlines():
        if "=" in line and " " not in line.strip().split("=", 1)[0]:
            k, v = line.rsplit("=", 1)
            if v.strip().isdigit():
                states[k.strip()] = int(v.strip())
    row["pg_active"] = states.get("active", 0)
    row["pg_idle"] = states.get("idle", 0)
    row["pg_states"] = ";".join(f"{k}={v}" for k, v in sorted(states.items()))
    wait_out = _run(
        [
            "docker",
            "exec",
            PG_CONTAINER,
            "psql",
            "-U",
            "ai_collection",
            "-d",
            "ai_collection",
            "-t",
            "-A",
            "-F",
            ":",
            "-c",
            "SELECT wevent || '=' || cnt FROM ("
            "SELECT COALESCE(wait_event_type,'none') AS wevent, COUNT(*) AS cnt "
            "FROM pg_stat_activity WHERE datname='ai_collection' GROUP BY 1) t ORDER BY 1;",
        ]
    )
    row["pg_wait_events"] = ";".join(
        line.strip()
        for line in wait_out.splitlines()
        if "=" in line and line.strip().split("=", 1)[1].strip().isdigit()
    )
    # Redis
    row["redis_dbsize"] = int(_run(["docker", "exec", REDIS_CONTAINER, "redis-cli", "-n", REDIS_DB, "dbsize"]).strip() or 0)
    csmp_scan = _run(["docker", "exec", REDIS_CONTAINER, "sh", "-c", f"redis-cli -n {REDIS_DB} --scan --pattern 'csmp:*' | wc -l"])
    row["redis_csmp_keys"] = int(csmp_scan.strip() or 0)
    degraded = _run(
        ["docker", "exec", REDIS_CONTAINER, "sh", "-c", f"redis-cli -n {REDIS_DB} --scan --pattern '*llm_degraded*'"]
    ).split()
    row["llm_degraded_keys"] = ";".join(sorted(set(degraded)))
    return row


def cmd_sample(args: argparse.Namespace) -> int:
    peer_ips = _docker_ips()
    print(f"[INFO] 基础设施 IP 映射={peer_ips}")
    out_path = Path(args.out) if args.out else REPORT_DIR / f"sample_{int(time.time())}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "ts",
        "rss_mib",
        "total_established",
        "pg",
        "redis",
        "mcp_gateway",
        "rocketmq",
        "pg_active",
        "pg_idle",
        "pg_states",
        "pg_wait_events",
        "redis_dbsize",
        "redis_csmp_keys",
        "llm_degraded_keys",
    ]
    n = 1
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        if args.once:
            row = _sample_once(peer_ips)
            writer.writerow(row)
            print(json.dumps(row, ensure_ascii=False, indent=2))
        else:
            deadline = time.time() + args.duration
            while time.time() < deadline:
                row = _sample_once(peer_ips)
                writer.writerow(row)
                fh.flush()
                print(f"sample#{n} {row['ts']} rss={row['rss_mib']}MiB " f"tcp={row['total_established']} pg_act={row['pg_active']} redis={row['redis_dbsize']}", flush=True)
                n += 1
                time.sleep(min(args.interval, max(0.0, deadline - time.time())))
    print(f"[ARTIFACT] {out_path}")
    return 0


# --- logmetrics：日志事件计数 -----------------------------------------------
def _docker_logs_since(container: str, since: str | None, until: str | None, minutes: float | None) -> str:
    cmd = ["docker", "logs", container]
    if since:
        cmd += ["--since", since]
    if until:
        cmd += ["--until", until]
    if minutes and not since:
        cmd += ["--since", f"{int(minutes)}m"]
    return _run(cmd, timeout=120)


def cmd_logmetrics(args: argparse.Namespace) -> int:
    since = args.since_rfc3339
    until = args.until_rfc3339
    containers = [COMPLAINT_CONTAINER]
    if args.include_gateway:
        containers.append(GATEWAY_CONTAINER)
    summary: dict[str, dict[str, int]] = {}
    hits_detail: dict[str, list[str]] = {k: [] for k in LOG_PATTERNS}
    for container in containers:
        logs = _docker_logs_since(container, since, until, args.minutes)
        bucket: dict[str, int] = {k: 0 for k in LOG_PATTERNS}
        for line in logs.splitlines():
            for name, pat in LOG_PATTERNS.items():
                if pat.search(line):
                    bucket[name] += 1
                    if len(hits_detail[name]) < 5:
                        hits_detail[name].append(f"{container}: {line[:300]}")
        summary[container] = bucket
    # Redis 降级键快照（当前状态；TTL 60s，窗口内可能已过期，日志事件为准）
    degraded_keys = _run(
        ["docker", "exec", REDIS_CONTAINER, "sh", "-c", f"redis-cli -n {REDIS_DB} --scan --pattern '*llm_degraded*'"]
    ).split()
    result = {
        "window": {"since": since, "until": until, "minutes": args.minutes},
        "counts": summary,
        "redis_llm_degraded_keys_now": sorted(set(degraded_keys)),
        "samples": hits_detail,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ARTIFACT] {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="complaint.created 事件入口压测（批次5）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pub = sub.add_parser("publish", help="一次性发布 N 条 complaint.created")
    p_pub.add_argument("--count", type=int, default=100, help="发布条数（默认 100）")
    p_pub.add_argument("--start-index", type=int, default=0, help="案池偏移（每档换段，默认 0）")
    p_pub.add_argument("--tier", default="na", help="档位标签：4/8/16/final，仅用于产物标注")
    p_pub.add_argument("--run-tag", default=None, help="覆盖产物 run_tag（默认 tier-时间戳）")
    p_pub.add_argument("--trace-prefix", default="lt5")
    p_pub.add_argument("--case-id-file", default=None, help="显式 case_id 清单（每行一个，优先于 SQL 案池）")
    p_pub.add_argument("--rocketmq-proxy", default=None, help="默认 localhost:18081")
    p_pub.add_argument("--dry-run", action="store_true", help="只打印选中 case 不发布")
    p_pub.set_defaults(func=cmd_publish)

    p_watch = sub.add_parser("watch", help="轮询 PG 对账到全部终态或超时")
    p_watch.add_argument("--artifact", required=True, help="publish 产物 jsonl 路径")
    p_watch.add_argument("--timeout", type=float, default=2400.0)
    p_watch.add_argument("--interval", type=float, default=5.0)
    p_watch.add_argument("--out", default=None)
    p_watch.set_defaults(func=cmd_watch)

    p_soak = sub.add_parser("soak", help="长稳周期性补发保持满载")
    p_soak.add_argument("--duration", type=int, default=1800, help="持续秒数（默认 1800=30 分钟）")
    p_soak.add_argument("--inflight-target", type=int, default=32, help="维持的在途目标量（建议=最终并行度×4）")
    p_soak.add_argument("--interval", type=int, default=20, help="补发轮询间隔秒")
    p_soak.add_argument("--burst", type=int, default=32, help="单轮补发上限")
    p_soak.add_argument("--start-index", type=int, default=0)
    p_soak.add_argument("--tier", default="final")
    p_soak.add_argument("--run-tag", default=None)
    p_soak.add_argument("--trace-prefix", default="lt5")
    p_soak.add_argument("--rocketmq-proxy", default=None)
    p_soak.set_defaults(func=cmd_soak)

    p_sample = sub.add_parser("sample", help="资源采样输出 CSV")
    p_sample.add_argument("--interval", type=int, default=300)
    p_sample.add_argument("--duration", type=int, default=1800)
    p_sample.add_argument("--once", action="store_true", help="单次采样（基线用）")
    p_sample.add_argument("--out", default=None)
    p_sample.set_defaults(func=cmd_sample)

    p_log = sub.add_parser("logmetrics", help="LLM/MCP 异常事件日志计数")
    p_log.add_argument("--since-rfc3339", default=None)
    p_log.add_argument("--until-rfc3339", default=None)
    p_log.add_argument("--minutes", type=float, default=None, help="相对窗口（最近 N 分钟）")
    p_log.add_argument("--include-gateway", action="store_true", default=True)
    p_log.add_argument("--out", default=None)
    p_log.set_defaults(func=cmd_logmetrics)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if asyncio.iscoroutinefunction(args.func):
        return asyncio.run(args.func(args))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

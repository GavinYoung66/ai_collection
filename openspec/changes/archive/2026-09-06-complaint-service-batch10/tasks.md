# complaint-service-batch10 Tasks

> 执行顺序：12.0 接线 → 12.1～12.4 先行 → 12.5～12.7 可并行。  
> 环境：conda `ai_collection`，Python≥3.11；`cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH`。

**硬约束**
- 无 `graph_service`；真图用 `build_complaint_graph`（+ 可选 `app/graph/runner.py`）
- Publisher 仅 `app/events/publisher.py`；禁止 `app/service/event_publisher.py`
- 11 降级 = 直调 degrade_*；不挂图
- 冒烟端口 **8006**；mode/review/close 用现网 **PUT**
- 覆盖率：核心模块 ≥85%（D8），非整包强制

---

## 0. 前置：judge run_graph 最小接线（D1）

- [x] 0.1 在 `complaint_service.judge` 实现 `run_graph=True`：调用 `build_complaint_graph` + ainvoke/invoke（或 `app/graph/runner.py`）；`False` 保持 stub
- [x] 0.2 `tests/unit/test_batch9.py` stub 路径仍通过；无 `graph_service` 模块

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_batch9.py -q
python3 -c "import importlib.util; assert importlib.util.find_spec('app.service.graph_service') is None; from app.graph.builder import build_complaint_graph; print('D1 OK')"
```

---

## 1. 12.1 单元测试强化

- [x] 1.1～1.5 强化 test_state_reducer / test_edges / test_fallback / test_mcp_client / test_evidence_helper
- [x] 1.6 核心覆盖率 ≥85%（按下述 cov 列表，可按仓库微调但须写进 CI）

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/ \
  --cov=app.models.state --cov=app.graph.edges \
  --cov=app.mcp.client --cov=app.mcp.errors \
  --cov=app.utils.evidence_helper --cov=app.service.recovery_service \
  --cov-report=term-missing --cov-fail-under=85 -q
```

---

## 2. 12.2 集成测试

- [x] 2.1 `tests/integration/conftest.py`（USE_REAL_INFRA 切换）
- [x] 2.2 `test_graph_flow.py`：1/2/3 轮与可注入故障
- [x] 2.3 `test_api_endpoints.py`：接单→judge(run_graph=true)→…→**PUT** review→**PUT** close
- [x] 2.4 `test_human_review.py`：扇出 + **`reject_rejudge`→evidence_re_gather**
- [x] 2.5 `test_degrade.py`：**直调** 11 场景 handle_*，断言码（含权限拒绝无码）
- [x] 2.6 `USE_REAL_INFRA=0` 全绿

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/integration/ -v
```

---

## 3. 12.3 E2E

- [x] 3.1 e2e conftest（TestClient 或 uvicorn）
- [x] 3.2 `test_complaint_journey.py`：run_graph=true；PUT review/close；`publisher` 断言 judged/closed
- [x] 3.3 确认不存在 `app/service/event_publisher.py`

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/e2e/ -v
test ! -f app/service/event_publisher.py
```

---

## 4. 12.4 冒烟

- [x] 4.1 扩展 `../../scripts/smoke/smoke_complaint_service.sh`（相对服务目录；仓库根脚本）
- [x] 4.2 health 对齐 `status=ok` + pg/redis/mcp；去掉错误的 `healthy` 唯一依赖
- [x] 4.3 业务项 + **PUT mode**（signers）
- [x] 4.4 [PASS]/[FAIL] + 退出码

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH uvicorn app.main:app --host 127.0.0.1 --port 8006 &
sleep 3 && bash ../../scripts/smoke/smoke_complaint_service.sh; echo "exit=$?"
```

---

## 5. 12.5 CI（可与 6/7 并行）

- [x] 5.1 新建 `.github/workflows/ci.yml`
- [x] 5.2 核心 cov-fail-under=85（D8 列表）
- [x] 5.3 有则启用 ruff/bandit/mypy
- [x] 5.4 PR 自检清单

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/ tests/integration/ \
  --cov=app.models.state --cov=app.graph.edges \
  --cov=app.mcp.client --cov=app.mcp.errors \
  --cov=app.utils.evidence_helper --cov=app.service.recovery_service \
  --cov-fail-under=85 -q
```

---

## 6. 12.6 Prompt 回归（可并行）

- [x] 6.1～6.2 golden_set 10～20 条，**仅四值 verdict**
- [x] 6.3 test_prompt_regression.py 一致率 ≥95%
- [x] 6.4 50+ 渐进策略已在 design（无需再改业务）

**验证：**
```bash
cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/regression/ -v
```

---

## 7. 12.7 压测（可并行）

- [x] 7.1 locustfile.py（stub 路径为主）
- [x] 7.2 run_loadtest.sh + docker-compose.dev.yml
- [x] 7.3 stub 基线门槛；低于则非 0；标明非 PRD 生产 SLA

**验证：**
```bash
cd services/complaint-service && bash scripts/loadtest/run_loadtest.sh --headless --users 50 --spawn-rate 10 --run-time 60s; echo "exit=$?"
```

---

## 8. 全量回归

- [x] 8.1 unit+integration+e2e+regression 全绿
- [x] 8.2 核心覆盖率 ≥85%
- [x] 8.3 冒烟 exit 0（port 8006）

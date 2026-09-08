# Tasks: complaint-service-batch8

## 执行顺序与依赖

```
0. redis_keys 增补
        ↓
8.1 session_service ──┐
                       ├─→ 8.2a review_service ─┐
                       │                          ├─→ 8.3 stats_service（+ 接线 stats 路由）
                       └─→ 8.2b review_sla_service┘        │
                                                           ├─→ 8.4 callback_service ─┐
                                                           │                          ├─→ 8.6 metrics_service
                                                           └─→ 8.5 backflow_service ──┘
```

- 8.1 先行；8.2a + 8.2b 并行；8.3 依赖 8.2；8.4 / 8.5 / 8.6 可并行
- 8.1 超时与 8.4 对账共用 `reconcile.py`

环境：conda `ai_collection`，`PYTHONPATH=../..:$PYTHONPATH`，在 `services/complaint-service/` 下执行。

**硬约束（P0/P1）**
- 路径一律 `services/complaint-service/`
- verdict 四值无 normal；reject_count 硬上限 2
- 收敛 complaint_service 门面，禁止平行真相源
- 锁 TTL=**1800s**；幂等靠 `complaint_id→session` 持久映射 + SETNX
- 错误码不新增：A5001/A5002/A5013 等见 design D10
- Redis / HTTP / MCP 可注入；单测禁止真网

---

## 0. redis_keys 增补（先于 8.1）

- [x] 修改 `app/utils/redis_keys.py`：
  - `TTL_COMPLAINT_LOCK = 1800`
  - `complaint_lock_key(complaint_id)` → `csmp:complaint:lock:{id}`
  - `callback_dlq_key()` → `csmp:callback:dlq`
  - `backflow_retry_key()` → `csmp:backflow:retry`
  - 确认 `hotspots_key` / `TTL_HOTSPOTS=21600` 已存在
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.utils import redis_keys as rk
  assert rk.TTL_COMPLAINT_LOCK == 1800
  assert rk.complaint_lock_key('c1') == 'csmp:complaint:lock:c1'
  assert 'callback:dlq' in rk.callback_dlq_key()
  assert 'backflow:retry' in rk.backflow_retry_key()
  print('0 redis_keys OK')
  "
  ```

---

## 8.1 session_service

- [x] 创建 `app/service/session_service.py`：持久映射幂等、SETNX TTL=1800、初始 pending、10 状态、30min 超时
- [x] 创建 `app/service/reconcile.py`：`daily_reconcile()`（draft_generated 超 1h + 超时扫描入口）
- [x] 收敛 `complaint_service.py`：薄门面委托 session_service（路由签名不变）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.session_service import SessionService
  from app.utils.redis_keys import TTL_COMPLAINT_LOCK
  assert TTL_COMPLAINT_LOCK == 1800
  s = SessionService()
  sid = s.create_session('complaint-1', '投诉文本')
  assert s.get_status(sid) == 'pending'
  sid2 = s.create_session('complaint-1', '投诉文本')
  assert sid == sid2
  s.transition(sid, 'evidence_gathering')
  assert s.get_status(sid) == 'evidence_gathering'
  print('8.1 session OK')
  "
  ```

---

## 8.2a review_service

- [x] 创建 `app/service/review_service.py`：6 动作、reject_count≤2、驳回原因、状态映射、agent_logs、human_review stub
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.session_service import SessionService
  from app.service.review_service import ReviewService
  sess = SessionService()
  sid = sess.create_session('c-rej', '文本')
  sess.transition(sid, 'pending_review')
  r = ReviewService(session_service=sess)
  r.submit(sid, 'reject_rejudge', reason='wrong_verdict')
  r.submit(sid, 'reject_rejudge', reason='wrong_verdict')
  r.submit(sid, 'reject_rejudge', reason='wrong_verdict')
  assert r.get_status(sid) == 'pending_senior_review'
  print('8.2a review reject_count<=2 OK')
  "
  ```

---

## 8.2b review_sla_service

- [x] 创建 `app/service/review_sla_service.py`：SLA 3 级、权限 4 级、ai_human_diff、复用 audit_service
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.review_sla_service import ReviewSLAService
  s = ReviewSLAService()
  assert s.can_submit('readonly') is False
  assert s.can_submit('level3') is True
  diff = s.compute_ai_human_diff(
      ai_verdict='agent_violation', human_verdict='agent_violation',
      ai_resp='a', human_resp='b')
  assert diff['verdict_match_rate'] == 1.0
  print('8.2b SLA+RBAC+diff OK')
  "
  ```

---

## 8.3 stats_service

- [x] 创建 `app/service/stats_service.py`：7 指标、by_verdict 四值、一致率告警、热点缓存 TTL 6h
- [x] 修改 `app/api/routes/stats.py`：委托 stats_service（禁止路由自算）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.stats_service import StatsService
  s = StatsService()
  st = s.get_stats()
  assert set(st['by_verdict'].keys()) == {
      'agent_violation','user_misunderstanding','compliant_but_poor_experience','pending'}
  assert 'p95_processing_minutes' in st
  from fastapi.testclient import TestClient
  from app.main import app
  r = TestClient(app).get('/api/v1/complaints/stats')
  assert r.json()['code'] == 0
  print('8.3 stats OK')
  "
  ```

---

## 8.4 callback_service

- [x] 创建 `app/service/callback_service.py`：幂等 session_id+version、3 次重试 10/30/60s、DLQ、对接 reconcile
- [x] 验证（内联 Mock，禁止未定义符号）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.callback_service import CallbackService

  class AlwaysFailHttp:
      def post(self, *a, **k):
          raise RuntimeError('down')

  c = CallbackService(http_client=AlwaysFailHttp())
  ok = c.send('sid-1', version=1, payload={})
  assert ok is False
  assert c.dlq_size() >= 1
  print('8.4 callback DLQ OK')
  "
  ```

---

## 8.5 backflow_service

- [x] 创建 `app/service/backflow_service.py`：write_session + write_human_review_truth、3 次失败暂存、A5013 告警、不阻塞
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.backflow_service import BackflowService

  class AlwaysFailMcp:
      async def call(self, *a, **k):
          raise RuntimeError('mcp down')
      def call_sync(self, *a, **k):
          raise RuntimeError('mcp down')

  b = BackflowService(mcp_client=AlwaysFailMcp())
  b.write_session('sid-1', {'state': 'x'})
  assert b.retry_queue_size() >= 1
  print('8.5 backflow zero-loss OK')
  "
  ```

---

## 8.6 metrics_service

- [x] 创建 `app/service/metrics_service.py`：自建 MetricsCollector、3 类指标、告警阈值、60s、MockClock
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.metrics_service import MetricsService, MockClock
  m = MetricsService(clock=MockClock())
  m.record('mcp_failure_rate', 0.06)
  alerts = m.evaluate_alerts()
  assert any(a['metric'] == 'mcp_failure_rate' for a in alerts)
  print('8.6 metrics alert OK')
  "
  ```

---

## 全流程集成验证

- [x] 创建 `tests/unit/test_batch8_services.py`：工单→session(pending)→judge stub→复核→回调/回流 Mock
- [x] verdict 四值无 normal；reject_count≤2；锁 TTL=1800
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest \
    tests/unit/test_batch8_services.py -v
  ```

---

## 完成标准

- [x] 全流程 Mock 跑通
- [x] 监控指标可 import + 埋点可断言
- [x] verdict 四值无 normal；reject_count≤2；锁 TTL=1800
- [x] 门面 + stats 路由单真相源；redis_keys 含本批 key
- [x] 路径仅 `services/complaint-service/`
- [x] 未做批次9 灰度矩阵 / 真 Pub-Sub / 真外部系统；未新增 A5015+

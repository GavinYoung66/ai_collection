# Tasks: complaint-service-batch9

## 执行顺序与依赖

```
0. redis_keys 增补
        ↓
9.1 mode_service ──┐
                   ├─→ 9.2a degrade_llm ─┐
9.5 rate_limiter ──┤                     ├─→ 9.3 recovery ─┐
                   ├─→ 9.2b degrade_mcp ─┤                  ├─→ 扩展 events/publisher → 11.2 停止致电
                   └─→ 9.2c degrade_flow┘                  │
                                                            └─→ 9.4 scheduler
```

- 9.1 + 9.5 先行；9.2a～c + 9.3～9.4 + 11.1～11.2 可并行
- 11.2 依赖 EventPublisher 扩展

环境：conda `ai_collection`，`PYTHONPATH=../..:$PYTHONPATH`，在 `services/complaint-service/` 下执行。

**硬约束（P0/P1）**
- 路径仅 `services/complaint-service/`
- 模式失败=**A5014**；A5013=回流；降级码以 design D1（common 语义）为准
- **只扩展** `app/events/publisher.py`；禁止 `app/service/event_publisher.py`
- 单工单锁用 **TTL_COMPLAINT_LOCK=1800**
- FakeRedis / MockClock / Mock MCP·LLM；单测禁真网
- 不重写 LangGraph 整图

---

## 0. redis_keys 增补

- [x] 修改 `app/utils/redis_keys.py`：
  - 确认 `TTL_COMPLAINT_LOCK == 1800`
  - `TTL_SESSION_FALLBACK = 259200`
  - `session_fallback_key(session_id)` → `csmp:session:{id}:fallback`
  - `mode_gray_sessions_key` / `queue_pending_key` → `csmp:mode:gray_sessions` / `csmp:queue:pending`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.utils import redis_keys as rk
  assert rk.TTL_COMPLAINT_LOCK == 1800
  assert rk.TTL_SESSION_FALLBACK == 259200
  assert 'fallback' in rk.session_fallback_key('s1')
  assert 'queue:pending' in rk.queue_pending_key()
  print('0 redis_keys OK')
  "
  ```

---

## 9.1 mode_service

- [x] 创建 `app/service/mode_service.py`：双模、灰度、达标全量、双签、失败回滚 A5014
- [x] 修改 `app/api/routes/mode.py`：委托 mode_service（禁止路由自持第二套状态）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.mode_service import ModeService
  from common.errors.codes import ErrorCode
  m = ModeService()
  ok, err = m.switch('multi_agent', signers=['ops'])
  assert ok is False and err == ErrorCode.A5014
  ok, err = m.switch('multi_agent', signers=['ops','tech'])
  assert ok is True and err is None
  # 失败回滚 A5014（脏 gray 值也不得抛 ValueError）
  class FailRedis:
      def get(self, k): return 'single_agent'
      def set(self, *a, **k): raise RuntimeError('down')
  ok, err = ModeService(redis_client=FailRedis()).switch('multi_agent', signers=['ops','tech'])
  assert ok is False and err == ErrorCode.A5014
  print('9.1 mode+A5014 OK')
  "
  ```

---

## 9.5 rate_limiter

- [x] 创建 `config/rate_limiter.yaml` + `app/service/rate_limiter.py`（5 维，锁用 TTL_COMPLAINT_LOCK）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.rate_limiter import RateLimiter
  from app.utils.redis_keys import TTL_COMPLAINT_LOCK
  assert TTL_COMPLAINT_LOCK == 1800
  r = RateLimiter()
  assert r.acquire_complaint('c1') is True
  assert r.acquire_complaint('c1') is False
  print('9.5 rate_limit OK')
  "
  ```

---

## 9.2a degrade_llm

- [x] 创建 `app/service/degrade_llm.py`：A5004 / A5010 / A5005
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.degrade_llm import DegradeLLM
  out = DegradeLLM().handle_judge_failure()
  assert 'A5004' in str(out.get('exception_info'))
  print('9.2a degrade_llm OK')
  "
  ```

---

## 9.2b degrade_mcp

- [x] 创建 `app/service/degrade_mcp.py`：A5006 / A5008 / A5013；权限拒绝仅审计
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.degrade_mcp import DegradeMCP
  assert DegradeMCP().handle_permission_denied('mcp', 'rag') is False
  print('9.2b degrade_mcp OK')
  "
  ```

---

## 9.2c degrade_flow

- [x] 创建 `app/service/degrade_flow.py`：4 场景 + publish complaint.degraded
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.degrade_flow import DegradeFlow
  from app.events.publisher import publisher
  publisher.clear()
  st = DegradeFlow().handle_loop_exceeded(loop_count=3)
  assert st == 'pending_review'
  assert len(publisher.list_events('complaint.degraded')) >= 1
  print('9.2c degrade_flow OK')
  "
  ```

---

## 9.3 recovery_service

- [x] 创建 `app/service/recovery_service.py`：checkpointer / fallback TTL 72h / 积压 / PG→A5010
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.recovery_service import RecoveryService
  assert RecoveryService().recover_from_fallback('sid-1')[0] is False
  print('9.3 recovery OK')
  "
  ```

---

## 9.4 scheduler

- [x] 创建 `app/service/scheduler.py`：stub + 5 job 函数
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.scheduler import cleanup_stale_24h
  assert cleanup_stale_24h() is not None
  print('9.4 scheduler OK')
  "
  ```

---

## 11.1 EventPublisher 扩展（单真相）

- [x] **扩展** `app/events/publisher.py`：4 频道 + complaint.degraded + case.risk.upgraded + trace_id
- [x] **禁止** 创建 `app/service/event_publisher.py`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.events.publisher import EventPublisher, publisher
  e = EventPublisher()
  e.publish('complaint.judged', {'trace_id':'t1'})
  assert e.list_events('complaint.judged')[0].payload['trace_id']=='t1'
  import os
  assert not os.path.exists('app/service/event_publisher.py')
  print('11.1 event_pub OK')
  "
  ```

---

## 11.2 停止致电联动

- [x] 修改 `review_service` / `complaint_service`：stop_call_flag + case.risk.upgraded
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.complaint_service import complaint_service
  from app.events.publisher import publisher
  publisher.clear()
  rec = complaint_service.create('x')
  complaint_service.judge(rec['complaint_id'])
  assert complaint_service.get_verdict(rec['complaint_id'])['stop_call_flag'] is True
  assert len(publisher.list_events('case.risk.upgraded')) >= 1
  print('11.2 stop_call OK')
  "
  ```

---

## 全流程单测

- [x] 创建 `tests/unit/test_batch9.py`：11 降级 + 限流 5 维 + 4 频道/扩展 topic + 停止致电 + A5014
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest tests/unit/test_batch9.py -v
  ```

---

## 完成标准

- [x] 灰度切换可用；11 降级单测覆盖；错误码符合 D1
- [x] 限流 5 维可测；Publisher 单真相；complaint.degraded / case.risk.upgraded 可断言
- [x] 停止致电可测；模式失败 A5014
- [x] 路径仅 `services/complaint-service/`
- [x] 未做批次10/11、真 PG 主备、真对象存储、真 MQ

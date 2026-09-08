# Tasks: complaint-service-batch7

## 执行顺序

模块7（7.1～7.5）与模块10（10.1～10.4）可并行。  
环境：conda `ai_collection`，`PYTHONPATH=../..:$PYTHONPATH`，在 `services/complaint-service/` 下执行。

**硬约束（P0）**
- `main.py` 挂载顺序：`stats` → `mode` → `complaints`（固定路径先于 `/{id}`）
- 模式切换失败错误码：**A5014**（在 `common/errors/codes.py` 增补）；A5013 仍为样本回流
- `judge` **默认 stub**，禁止冒烟默认同步跑 interrupt 全图
- 成功响应 `code` 为 int `0`；错误为 str `"A50xx"`

---

## 0. 错误码增补（先于 7.3）

- [x] 修改 `common/errors/codes.py`：新增 `ErrorCode.A5014` + 消息「模式切换失败」
- [x] 验证：`assert ErrorCode.A5014.value == "A5014"`

---

## 模块7 API 接口层

### 7.1 complaints 路由 + schemas + 薄 stub

- [x] 创建 `app/api/schemas/complaint.py`、`app/service/complaint_service.py`（内存 stub）
- [x] 创建 `app/api/routes/complaints.py`：列表/详情/judge(stub)/status/verdict/agent-logs/review（**尚不含**与 stats 冲突的挂载问题——close 可同文件）
- [x] 统一响应工具：`code: int | str`
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from fastapi.testclient import TestClient
  from app.main import app
  c = TestClient(app)
  r = c.post('/api/v1/complaints', json={'complaint_text':'投诉催收'})
  assert r.json()['code'] == 0 and isinstance(r.json()['code'], int)
  cid = r.json()['data']['complaint_id']
  j = c.post(f'/api/v1/complaints/{cid}/judge')
  assert j.json()['code'] == 0
  print('7.1 complaints+judge stub OK')
  "
  ```

### 7.2 stats 路由

- [x] 创建 `app/api/routes/stats.py`
- [x] 验证（**必须在 complaints 挂载后仍命中统计**）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from fastapi.testclient import TestClient
  from app.main import app
  c = TestClient(app)
  r = c.get('/api/v1/complaints/stats')
  assert r.json()['code'] == 0
  bv = r.json()['data']['by_verdict']
  assert set(bv.keys()) == {'agent_violation','user_misunderstanding','compliant_but_poor_experience','pending'}
  print('7.2 stats OK')
  "
  ```

### 7.3 mode 路由

- [x] 创建 `app/api/routes/mode.py`；FakeRedis；失败回滚返回 **A5014**
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from fastapi.testclient import TestClient
  from app.main import app
  c = TestClient(app)
  r = c.put('/api/v1/complaints/mode', json={'mode':'multi_agent','gray_percent':100})
  assert r.json()['code'] == 0
  print('7.3 mode OK')
  "
  ```

### 7.4 close + stub publisher

- [x] `PUT /complaints/{id}/close`；`EventPublisher` stub 可断言
- [x] 验证：create → close → code=0

### 7.5 错误码 + 挂载顺序

- [x] `main.py`：`include_router(stats)` → `include_router(mode)` → `include_router(complaints)`，前缀 `/api/v1`
- [x] `register_exception_handlers`；`routes/__init__.py` 汇总
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from common.errors.codes import ErrorCode
  assert all(hasattr(ErrorCode, f'A50{i:02d}') for i in range(1,14))
  assert ErrorCode.A5014.value == 'A5014'
  from fastapi.testclient import TestClient
  from app.main import app
  c = TestClient(app)
  assert c.get('/api/v1/complaints/stats').json()['code'] == 0
  print('7.5 mount+codes OK')
  "
  ```

---

## 模块10 安全护栏

### 10.1 MCP 二次脱敏（补全，不重写）

- [x] 在现有 `mask_text` 路径上补：命中改写→告警 + `audit.log_action`
- [x] 验证（走 Client，非仅测 common）：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  import asyncio
  from unittest.mock import patch, AsyncMock
  from app.mcp.client import MCPClient
  # 视实现注入 transport/mock，使 call 返回含明文手机号的 dict/str，再 assert 脱敏
  print('10.1 implement MCPClient path test in tests/unit/test_security.py')
  "
  ```

  正式断言放在 `tests/unit/test_security.py::test_mcp_client_masks_pii`。

### 10.2 落库前脱敏

- [x] `app/service/security.py`：`mask_before_persist`；挂 `provider.py` 关键路径
- [x] 验证：`mask_before_persist('身份证110101199001011234')` 已脱敏

### 10.3 RBAC 审计

- [x] `app/service/audit_service.py`：`can_access_plaintext(role)` 等
- [x] 验证：developer=False；operator_final=True

### 10.4 L4 后置校验

- [x] `guardrails_postcheck.py`：按 design D8；挂钩 verdict API / generate_package（可调用）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -c "
  from app.service.guardrails_postcheck import postcheck
  from app.models.state import VerdictDraft
  bad, _ = postcheck(VerdictDraft.model_construct(verdict='normal', confidence=0.9), rag_doc_ids=[])
  assert bad is False or (isinstance(bad, tuple) and bad[0] is False) or bad[0] is False
  # 以实现签名为准：assert not ok
  print('10.4 use unit test for model_construct normal')
  "
  ```

  正式用例：`test_postcheck_rejects_normal_via_model_construct`。

---

## 单测

- [x] `tests/unit/test_api_routes.py`：**12 路由** + stats 不被 {id} 吞 + A5014 模式失败（可 mock redis）+ judge 快速返回
- [x] `tests/unit/test_security.py`：MCP 脱敏路径 / mask_before_persist / rbac / L4（含 normal construct）
- [x] 验证：

  ```bash
  cd services/complaint-service && PYTHONPATH=../..:$PYTHONPATH python3 -m pytest \
    tests/unit/test_api_routes.py tests/unit/test_security.py -v
  ```

---

## 全部完成标准

- [x] 12 路由 Mock 可调通；stats 路由正确
- [x] 统一响应：成功 code int 0 / 错误 code str
- [x] A5001～A5013 + A5014；挂载顺序正确
- [x] judge 默认 stub；VerdictType 无 normal
- [x] 脱敏/审计/L4 可测；红线范围符合 D8
- [x] 未做批次8 整业务层 / 批次9 矩阵 / 真 Pub-Sub
- [x] 路径仅 `services/complaint-service/`

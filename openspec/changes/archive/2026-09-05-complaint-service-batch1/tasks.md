# Tasks: complaint-service-batch1

## 执行顺序

严格串行：**1.1 → 1.2 → 1.3 → 1.4 → 1.5**。一次 apply 做完。

---

## 1.1 目录结构

- [x] 创建 `app/` 下全部子包 `__init__.py`：`api/`、`api/routes/`、`graph/`、`graph/nodes/`、`mcp/`、`llm/`、`prompt/`、`prompt/templates/`、`models/`、`service/`、`utils/`
- [x] 保留批次0 已有文件：`app/prompt/templates/*.yaml`（8 个）、`tests/mock/`、`scripts/mock/`，不删除不覆盖
- [x] 验证：

  ```bash
  cd services/complaint-service && for d in api api/routes graph graph/nodes mcp llm prompt prompt/templates models service utils; do test -f app/$d/__init__.py && echo "OK $d" || echo "MISSING $d"; done
  ```

  且 8 个 YAML 仍存在：

  ```bash
  ls app/prompt/templates/*.yaml | wc -l  # 应输出 8
  ```

## 1.2 配置体系（app/config.py）

- [x] 创建 `app/config.py`，定义 `ComplaintSettings(Settings)`，字段与默认值：
  - `COMPLAINT_MAX_LOOPS: int = 3`
  - `COMPLAINT_TASK_TIMEOUT: int = 1800`
  - `COMPLAINT_MAX_TOKENS: int = 50000`
  - `COMPLAINT_MAX_ACTIVE: int = 20`
  - `MCP_GATEWAY_MODE: str = "inline"`
  - `LLM_MODEL_PRIMARY: str = "deepseek-r1"`
  - `LLM_MODEL_FALLBACK_1: str = "qwen-plus"`
  - `LLM_MODEL_FALLBACK_2: str = "qwen-turbo"`
  - `LLM_DEEPSEEK_API_KEY: str = "change_me"`
  - `LLM_DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"`
  - `LLM_DASHSCOPE_API_KEY: str = "change_me"`
- [x] 模块级单例 `settings = ComplaintSettings()`
- [x] 验证：

  ```bash
  cd services/complaint-service && python3 -c "
  from app.config import settings
  assert settings.COMPLAINT_MAX_LOOPS == 3
  assert settings.LLM_MODEL_PRIMARY == 'deepseek-r1'
  assert settings.COMPLAINT_MAX_ACTIVE == 20
  assert settings.MCP_GATEWAY_MODE == 'inline'
  print('config OK')
  "
  ```

## 1.3 依赖声明（pyproject.toml）

- [x] 创建/更新 `services/complaint-service/pyproject.toml`，dependencies 含：
  - `langgraph>=0.0.40`
  - `langgraph-checkpoint-postgres`
  - `asyncpg>=0.29.0`
- [x] 其余依赖复用全局 `requirements.lock` 约定（fastapi/uvicorn/sqlalchemy/pydantic-settings 等不在此重复声明或按项目既有约定）
- [x] 验证：

  ```bash
  cd services/complaint-service && grep -E "langgraph|asyncpg" pyproject.toml
  ```

## 1.4 依赖注入（app/api/deps.py）

- [x] 创建 `app/api/deps.py`：
  - `get_session`：复用 `common.db.session.get_session`（不重写）
  - `get_mcp_client()`：单例工厂，批次1 返回 `None` 占位（真实实现留批次3）
  - `get_llm_provider()`：单例工厂，批次1 返回 `None` 占位（真实实现留批次5）
- [x] 验证：

  ```bash
  cd services/complaint-service && python3 -c "
  from app.api.deps import get_session, get_mcp_client, get_llm_provider
  # get_session 是 async generator 函数
  import inspect
  assert inspect.isasyncgenfunction(get_session)
  assert get_mcp_client() is None
  assert get_llm_provider() is None
  print('deps OK')
  "
  ```

## 1.5 FastAPI 入口（app/main.py）

- [x] 创建/更新 `app/main.py`：
  - `FastAPI` 实例 + `lifespan`（startup/shutdown）
  - startup：预留 AsyncPostgresSaver + MCP Client 初始化（try/except，失败标记 degraded，不抛异常）
  - shutdown：预留连接池清理
  - 注册 `common.trace.middleware.TraceMiddleware`
  - 注册 `common.errors.handlers.register_exception_handlers(app)`
  - 健康检查路由：`GET /health` 返回 200，响应体含 `pg`/`mcp` 状态（degraded 时仍 200）
- [x] 验证（配置可 import + TestClient health=200）：

  ```bash
  cd services/complaint-service && python3 -c "
  from fastapi.testclient import TestClient
  from app.main import app
  client = TestClient(app)
  r = client.get('/health')
  assert r.status_code == 200, r.status_code
  body = r.json()
  assert 'status' in body
  print('health 200 OK', body)
  "
  ```

- [x] 验证 uvicorn 可启动（无 PG/MCP 配置时 degraded 启动）：

  ```bash
  cd services/complaint-service && timeout 8 uvicorn app.main:app --host 127.0.0.1 --port 8006 &
  sleep 3
  curl -s http://127.0.0.1:8006/health | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'; print('uvicorn health OK', d)"
  kill %1 2>/dev/null
  ```

---

## 全部完成标准

- [x] `services/complaint-service/` 下可 `uvicorn app.main:app` 启动，不依赖 PG/MCP 就绪
- [x] 健康检查返回 HTTP 200
- [x] 配置可 import，默认值与任务清单一致
- [x] deps.py 的 get_session 复用 common，MCP/LLM 工厂可调用（返回 None 占位）
- [x] 未实现业务 API/ORM/LangGraph/真实 MCP/真实 LLM（仅脚手架）

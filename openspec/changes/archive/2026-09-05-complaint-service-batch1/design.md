# Design: complaint-service-batch1

## 1. 目录结构（对齐 PRD 第23章）

```
services/complaint-service/
├── app/
│   ├── __init__.py
│   ├── main.py                    # 1.5 FastAPI 入口
│   ├── config.py                  # 1.2 配置
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                # 1.4 依赖注入
│   │   ├── health.py              # 已有（批次0 health 空壳，批次1 增强）
│   │   └── routes/
│   │       └── __init__.py        # 路由聚合（批次7 填实）
│   ├── graph/
│   │   ├── __init__.py
│   │   └── nodes/
│   │       └── __init__.py        # 批次4 填实
│   ├── mcp/
│   │   └── __init__.py            # 批次3 填实
│   ├── llm/
│   │   └── __init__.py            # 批次5 填实
│   ├── prompt/
│   │   ├── __init__.py
│   │   └── templates/             # 批次0 已有 8 个 YAML（保留）
│   ├── models/
│   │   └── __init__.py            # 批次2 填实
│   ├── service/
│   │   └── __init__.py            # 批次8 填实
│   └── utils/
│       └── __init__.py            # 批次2 填实
├── tests/                         # 批次0 已有（保留）
├── scripts/                       # 批次0 已有 mock/（保留）
└── pyproject.toml                 # 1.3
```

**保留策略**：批次0 已有的 `app/prompt/templates/*.yaml`、`tests/mock/`、`scripts/mock/` 全部保留，不删除、不覆盖。

## 2. 配置继承设计

```python
# app/config.py
from common.config.settings import Settings

class ComplaintSettings(Settings):
    COMPLAINT_MAX_LOOPS: int = 3
    COMPLAINT_TASK_TIMEOUT: int = 1800
    COMPLAINT_MAX_TOKENS: int = 50000
    COMPLAINT_MAX_ACTIVE: int = 20
    MCP_GATEWAY_MODE: str = "inline"          # inline | standalone
    LLM_MODEL_PRIMARY: str = "deepseek-r1"
    LLM_MODEL_FALLBACK_1: str = "qwen-plus"
    LLM_MODEL_FALLBACK_2: str = "qwen-turbo"
    LLM_DEEPSEEK_API_KEY: str = "change_me"
    LLM_DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    LLM_DASHSCOPE_API_KEY: str = "change_me"

settings = ComplaintSettings()
```

- 继承 `common.config.settings.Settings`（已配 `env_file=".env"`, `extra="ignore"`）
- 复用 Settings 已有的 PG/Redis/AES/JWT 等字段，不重复声明
- 单例 `settings` 在模块加载时实例化

## 3. 依赖声明策略

`pyproject.toml` 补充：
```toml
dependencies = [
    "langgraph>=0.0.40",
    "langgraph-checkpoint-postgres",
    "asyncpg>=0.29.0",
    # 其余依赖（fastapi/uvicorn/sqlalchemy/pydantic-settings 等）复用全局 requirements.lock
]
```

## 4. 依赖注入设计（app/api/deps.py）

```python
from common.db.session import get_session
from typing import Optional

# DB session：直接复用 common
get_session = get_session  # async generator, yield AsyncSession

# MCP Client 单例（批次1 占位，批次3 替换为真实 MCPClient）
_mcp_client = None

def get_mcp_client() -> Optional[object]:
    global _mcp_client
    if _mcp_client is None:
        # 批次1：返回 None 占位；startup 中根据配置尝试初始化，失败保持 None
        pass
    return _mcp_client

# LLM Provider 单例（批次1 占位，批次5 替换为真实 LLMProvider）
_llm_provider = None

def get_llm_provider() -> Optional[object]:
    global _llm_provider
    if _llm_provider is None:
        pass
    return _llm_provider
```

- `get_session` 直接复用 `common.db.session.get_session`，不重写
- MCP/LLM 工厂在批次1 返回 `None`，由 `main.py` startup 尝试初始化，失败保持 `None`
- 真实实现留批次3（MCP）、批次5（LLM）

## 5. FastAPI 入口设计（app/main.py）

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from common.trace.middleware import TraceMiddleware
from common.errors.handlers import register_exception_handlers
from app.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup：尝试初始化 AsyncPostgresSaver + MCP Client
    # 未配置时跳过，标记 degraded，不抛异常
    try:
        # 预留 AsyncPostgresSaver 初始化（批次4 接入）
        pass
    except Exception:
        app.state.pg_degraded = True
    try:
        # 预留 MCP Client 初始化（批次3 接入）
        pass
    except Exception:
        app.state.mcp_degraded = True
    yield
    # shutdown：清理连接池
    # 预留 engine.dispose() 等（批次4 接入）

app = FastAPI(title="complaint-service", lifespan=lifespan)
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)

# 健康检查：批次1 至少返回 200，探活字段可标记 degraded
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "pg": "degraded" if getattr(app.state, "pg_degraded", False) else "ok",
        "mcp": "degraded" if getattr(app.state, "mcp_degraded", False) else "ok",
    }
```

- 复用 `common.trace.middleware.TraceMiddleware`、`common.errors.handlers.register_exception_handlers`
- `lifespan` 中 startup/shutdown 预留 PG/MCP 初始化与清理，try/except 包裹确保不因缺依赖崩溃
- 健康检查始终返回 200，PG/MCP 状态通过字段标记

## 6. 复用 common 接口清单

| common 接口 | 用途 |
|-------------|------|
| `common.config.settings.Settings` | 配置基类 |
| `common.config.logging.setup_logging` | 日志初始化 |
| `common.db.session.get_session` | DB session 注入 |
| `common.db.session.async_session_factory` | 会话工厂 |
| `common.trace.middleware.TraceMiddleware` | 链路追踪中间件 |
| `common.errors.handlers.register_exception_handlers` | 异常处理器 |

## 7. 降级启动策略

批次1 的核心约束是**不因缺 PG/MCP 导致进程起不来**：
- PG 连接失败 → `app.state.pg_degraded = True`，health 标记 degraded
- MCP 初始化失败 → `app.state.mcp_degraded = True`，health 标记 degraded
- health 接口 HTTP 状态码始终 200（服务存活），降级状态在响应体中体现
- 批次1 不实现真实 PG/MCP 连接，仅预留 try/except 骨架

## 8. 执行顺序与依赖

严格串行 1.1→1.2→1.3→1.4→1.5：
- 1.1 目录结构：先建骨架，后续文件才有地方放
- 1.2 配置：deps/main 依赖 settings
- 1.3 依赖：langgraph/asyncpg 需在 deps/main import 前安装（实际不阻塞，但声明在前）
- 1.4 deps：依赖 config
- 1.5 main：依赖 config + deps

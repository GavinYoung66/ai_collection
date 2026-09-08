## MODIFIED Requirements

### Requirement: 依赖注入工厂

系统 SHALL 在 `app/api/deps.py` 提供：
- `get_session()`：复用 `common.db.session.get_session`（async generator，yield AsyncSession）
- `get_mcp_client()`：本服务 MCP Client 单例工厂，返回可注入真实 `MCPClient` 实现或可切换 Mock（不再永久返回 None）
- `get_llm_provider()`：本服务 LLM Provider 单例工厂，返回可注入真实 `ComplaintLLMProvider` 实现或可切换 Mock（不再永久返回 None）

#### Scenario: get_session 可注入

- **WHEN** FastAPI 依赖项使用 `Depends(get_session)`
- **THEN** 获得 `common.db.session` 的 AsyncSession

#### Scenario: MCP/LLM 单例工厂返回可调用对象

- **WHEN** 调用 `get_mcp_client()` 或 `get_llm_provider()`
- **THEN** 返回可调用对象（真实实现或 Mock），不返回 None，不抛异常，不阻塞启动

#### Scenario: 可切换 Mock 与真实实现

- **WHEN** 配置项指定使用 Mock（如测试环境）
- **THEN** `get_mcp_client()` / `get_llm_provider()` 返回 Mock 实例；配置指定真实时返回真实实现

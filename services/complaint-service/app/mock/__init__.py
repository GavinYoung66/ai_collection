"""应用内置 Mock 模块（T121，批次8）。

批次0 的 Mock 实现原本位于 tests/mock/，生产代码（USE_MOCK_* 懒加载分支）
反向依赖 tests.* 导致镜像必须拷贝 tests/ 目录。本包把被生产代码引用的
Mock 模块迁入 app/mock/（含 fixtures 运行时资源），生产镜像不再依赖 tests/：
- app.mock.mcp.mock_client / errors：MockMCPClient（4 Server × 14 tool）
- app.mock.llm.mock_provider：MockLLMProvider（fixture 驱动）
- app.mock.data.seed_complaints_v2：V2 演示种子数据

tests/mock/ 同名模块保留为薄转发层，旧导入路径（from tests.mock...）零改动兼容。
"""
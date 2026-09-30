"""complaint-service 专有配置，继承 common Settings。"""
from __future__ import annotations

from common.config.settings import Settings


class ComplaintSettings(Settings):
    """投诉研判服务配置，复用 common Settings 已有字段（PG/Redis/AES/JWT 等）。"""

    # ── 业务参数 ──
    COMPLAINT_MAX_LOOPS: int = 3
    COMPLAINT_TASK_TIMEOUT: int = 1800
    COMPLAINT_MAX_TOKENS: int = 50000
    COMPLAINT_MAX_ACTIVE: int = 20

    # ── 日志级别（四环境 env 注入；接 setup_logging）──
    LOG_LEVEL: str = "INFO"

    # ── MCP 网关模式 ──
    MCP_GATEWAY_MODE: str = "inline"  # inline（dev 进程内） | standalone（prod 独立容器）
    MCP_GATEWAY_URL: str = ""         # standalone 模式下的网关地址（非 rag_kb 工具转发）
    # 真路径 rag_kb bridge（D2）：USE_MOCK_MCP=false 时必填，如 http://rag-service:8000
    RAG_SERVICE_URL: str = ""
    # 批次③ 进程内桥（mcp-real-path）：data-platform 快照/qc-source、qc-service 质检台账
    DATA_PLATFORM_URL: str = "http://data-platform:8000"
    QC_SERVICE_URL: str = "http://qc-service:8000"
    # 进程内桥 HTTP 可靠性：沿用内部 HTTP 5s 超时 / 3 次重试（design D7）
    BRIDGE_HTTP_TIMEOUT: float = 5.0
    BRIDGE_HTTP_RETRY: int = 3

    # ── Mock 开关（dev 默认 Mock，prod 关闭）──
    USE_MOCK_MCP: bool = True
    USE_MOCK_LLM: bool = True

    # 仿真：judge 停在 human_review 后同栈 auto-approve（默认关；验收 compose 显式 true）
    SIM_AUTO_REVIEW: bool = False

    # ── CoT 加密密钥（dev stub，不接 KMS；prod 由 KMS 注入）──
    LLM_COT_KEY: str = "dev_cot_key_stub_change_me"

    # ── LLM 模型路由 ──
    LLM_MODEL_PRIMARY: str = "deepseek-r1"
    LLM_MODEL_FALLBACK_1: str = "qwen-plus"
    LLM_MODEL_FALLBACK_2: str = "qwen-turbo"
    # fallback_chain 全链（逗号分隔，顺序即优先级：主模型 → 备用 → 更弱模型）。
    # 留空则由 LLM_MODEL_PRIMARY / FALLBACK_1 / FALLBACK_2 推导（向后兼容）。
    LLM_FALLBACK_CHAIN: str = ""

    # ── LLM API 凭据 ──
    LLM_DEEPSEEK_API_KEY: str = "change_me"
    LLM_DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    LLM_DASHSCOPE_API_KEY: str = "change_me"
    LLM_DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 兼容仓库根 .env 常见别名（个人项目）
    DEEPSEEK_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""

    # ── LangGraph checkpointer 持久化（批次4）──
    # COMPLAINT_PG_DSN：显式覆盖，优先级最高；留空且 CHECKPOINTER_PG=true 时由 PG_* 派生。
    # COMPLAINT_CHECKPOINTER_PG：总开关。默认 false → dev/test 走 MemorySaver，零行为变化。
    #   不做「有 PG_HOST 就自动派生」——PG_HOST 默认 localhost，自动派生会让本地所有
    #   judge 用例吃连接超时后降级，每个慢十几秒。必须显式开启。
    COMPLAINT_PG_DSN: str = ""
    COMPLAINT_CHECKPOINTER_PG: bool = False
    # session 持久化总开关。实际生效还要求 USE_MOCK_MCP=false（与 _persist_complaint_row
    # 同一道门），保证单测默认不碰 PG。回滚时置 false 即退回纯内存语义。
    COMPLAINT_SESSION_PERSIST: bool = True
    # psycopg 连接池大小（仅 CHECKPOINTER_PG=true 时生效）
    COMPLAINT_PG_POOL_MIN: int = 5
    COMPLAINT_PG_POOL_MAX: int = 20

    # ── LangSmith 追踪配置已上收 common Settings（跨服务统一开关）──
    # 见 common/trace/langsmith_gate.py；本服务默认 project 由闸门 fallback 提供


settings = ComplaintSettings()

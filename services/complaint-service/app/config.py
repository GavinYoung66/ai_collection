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

    # ── Mock 开关（dev 默认 Mock，prod 关闭）──
    USE_MOCK_MCP: bool = True
    USE_MOCK_LLM: bool = True

    # ── CoT 加密密钥（dev stub，不接 KMS；prod 由 KMS 注入）──
    LLM_COT_KEY: str = "dev_cot_key_stub_change_me"

    # ── LLM 模型路由 ──
    LLM_MODEL_PRIMARY: str = "deepseek-r1"
    LLM_MODEL_FALLBACK_1: str = "qwen-plus"
    LLM_MODEL_FALLBACK_2: str = "qwen-turbo"

    # ── LLM API 凭据 ──
    LLM_DEEPSEEK_API_KEY: str = "change_me"
    LLM_DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    LLM_DASHSCOPE_API_KEY: str = "change_me"
    LLM_DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 兼容仓库根 .env 常见别名（个人项目）
    DEEPSEEK_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""

    # ── LangSmith 追踪配置已上收 common Settings（跨服务统一开关）──
    # 见 common/trace/langsmith_gate.py；本服务默认 project 由闸门 fallback 提供


settings = ComplaintSettings()

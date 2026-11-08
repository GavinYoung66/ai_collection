"""全局配置单例，pydantic-settings + .env 注入。

仅放跨服务平台项（库连接、共享密钥占位、公共超时等）。
RAG 产品开关与检索/入库参数见 `services/rag-service/app/rag_settings.py`。
"""
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 环境
    ENV: str = "dev"
    # 认证总开关：生产恒为开启（见 auth_required）；本地可显式关闭，启动时打印显著告警
    ENABLE_AUTH: bool = False

    # PostgreSQL
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "ai_collection"
    PG_PASSWORD: str = "change_me"
    PG_DB: str = "ai_collection"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # Milvus / Elasticsearch / Embedding（基础设施；rag 与其它服务可共用）
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    ES_HOST: str = "localhost"
    ES_PORT: int = 9200
    EMBEDDING_SERVICE_URL: str = "http://localhost:8100"
    RERANK_SERVICE_URL: str = "http://localhost:8101"

    # LLM（跨服务 Key / 默认模型；业务开关不放这里）
    LLM_API_KEY: str = "change_me"
    LLM_MODEL: str = "qwen-plus"
    LLM_TIMEOUT: int = 10
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DASHSCOPE_API_KEY: str = ""

    # 链路追踪（跨服务统一开关；唯一事实源，各服务不再自判）
    # LANGSMITH_ENABLED=false 时全线不建 run / 不写 SDK 环境变量 / 不外链
    LANGSMITH_ENABLED: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = ""
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"
    LANGSMITH_TRACE_URL_TEMPLATE: str = ""

    # Langfuse 链路追踪（与 LangSmith 同构；本地自托管默认 http://localhost:3000）
    # LANGFUSE_ENABLED=false 时全线不建 span / 不写 SDK 环境变量
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_BASE_URL: str = "http://localhost:3000"

    # ASR / TTS / CCC（呼叫相关公共密钥占位）
    ASR_APP_KEY: str = "change_me"
    ASR_ACCESS_TOKEN: str = "change_me"
    TTS_APP_KEY: str = "change_me"
    TTS_ACCESS_TOKEN: str = "change_me"
    CCC_ACCESS_KEY_ID: str = "change_me"
    CCC_ACCESS_KEY_SECRET: str = "change_me"

    # 种子脚本口令（scripts/seed/seed_users.py；生产禁止执行 seed，仅本地/测试注入）
    SEED_ADMIN_PASSWORD: str = ""
    SEED_SUPERVISOR_PASSWORD: str = ""
    SEED_AGENT_PASSWORD: str = ""
    SEED_QC_REVIEWER_PASSWORD: str = ""
    SEED_AI_TRAINER_PASSWORD: str = ""

    # AES / JWT
    AES_KEY: str = "change_me"
    JWT_SECRET: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24
    JWT_REFRESH_HOURS: int = 168
    # 网关鉴权：iss/aud 为空字符串表示未配置，跳过对应 claims 校验（零回归）。
    JWT_ISSUER: str = ""
    JWT_AUDIENCE: str = ""
    # API Key 白名单：逗号分隔、无空格，如 "ak-xxx,ak-yyy"；空表示禁用 API Key 方案。
    API_KEYS: str = ""
    # API Key 对应的身份角色默认值（API Key 本身不带 role，统一赋予此角色）。
    API_KEY_ROLE: str = "admin"

    # T68：代理头信任开关——仅在可信内网 LB/网关之后开启。默认 false 时
    # get_client_ip 一律回退 ASGI client（直连场景零回归，X-Real-IP/XFF 不被采信）。
    TRUST_PROXY_HEADERS: bool = False

    # LLM 可靠性（平台级：common/llm 跨服务共享的熔断阈值，业务参数不放这里）
    LLM_CIRCUIT_BREAKER_THRESHOLD: int = 3
    LLM_CIRCUIT_BREAKER_RECOVERY: int = 60

    # 事件总线契约（平台级，逗号分隔；新增事件类型只改配置，重启后生效，无需改代码）
    # 未登记的 event_name 一律拒收（fail-closed）。
    EVENT_ALLOWED_TYPES: str = (
        "complaint.created,complaint.result.ready,complaint.judged,complaint.closed,"
        "complaint.insight.weekly,complaint.mode.switched,complaint.degraded,"
        "case.risk.upgraded,callback.requested,qc.violation.reported,"
        "call.session.ended,etl.task.done,kb.sensitive_updated,strategy.updated,"
        "qc.check.completed,qc.complaint.suspected,qc.alert.raised,"
        "qc.stop_call.detected,qc.review.done,system.alert.raised"
    )
    # envelope.data 允许字段白名单（逗号分隔；仅 ID 类字段，禁止业务内容入总线）
    EVENT_DATA_WHITELIST: str = (
        "case_id,session_id,qc_record_id,complaint_no,backfill_date"
    )

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() in {"prod", "production"}

    @property
    def auth_required(self) -> bool:
        """认证是否强制：生产环境恒为 true（忽略 ENABLE_AUTH=false），本地可关闭调试。"""
        return self.is_production or self.ENABLE_AUTH

    @property
    def pg_dsn(self) -> str:
        # T115：用户/密码统一 URL 编码（spec runtime-correctness：DSN 构造单源
        # 收敛）——密码含 `@ : / # ?` 等保留字符时未编码拼接会被 SQLAlchemy
        # URL 解析错位（启动失败或连错 host）。用 quote(safe="") 而非 quote_plus：
        # 后者把空格编成 `+`，而 URL 解析/ConfigParser 出的 DSN 空格语义错位。
        return f"postgresql+asyncpg://{quote(self.PG_USER, safe='')}:{quote(self.PG_PASSWORD, safe='')}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"

    @property
    def pg_dsn_sync(self) -> str:
        return f"postgresql+psycopg2://{quote(self.PG_USER, safe='')}:{quote(self.PG_PASSWORD, safe='')}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"

    @property
    def pg_dsn_psycopg3(self) -> str:
        """psycopg3 / psycopg_pool 用的裸 DSN。

        方言注意：pg_dsn 是 postgresql+asyncpg://（SQLAlchemy asyncpg）、
        pg_dsn_sync 是 postgresql+psycopg2://（SQLAlchemy psycopg2），二者都带 "+driver"。
        psycopg_pool 直接吃 libpq 语义的 URL，带 "+driver" 会解析失败，
        故此处返回不带 driver 的 postgresql://。
        """
        return f"postgresql://{quote(self.PG_USER, safe='')}:{quote(self.PG_PASSWORD, safe='')}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"

    @property
    def es_url(self) -> str:
        return f"http://{self.ES_HOST}:{self.ES_PORT}"

    @property
    def deepseek_api_key_resolved(self) -> str:
        """DeepSeek Key：优先 DEEPSEEK_API_KEY，兼容 pydantic 未映射的 DeepSeek_API_KEY env。"""
        if self.DEEPSEEK_API_KEY:
            return self.DEEPSEEK_API_KEY
        import os

        return os.environ.get("DeepSeek_API_KEY", "")


_PLACEHOLDER_SECRETS = {"", "change_me", "<change_me>", "changeme", "change-me", "none", "null"}


def validate_required_secrets(cfg) -> None:
    """启动 fail-fast：JWT / AES 密钥不得为默认占位值；AES 密钥还须格式合法（T41）。

    - 占位/空值：立即失败，不打印密钥内容；生产由 KMS / 密钥系统注入。
    - AES_KEY 格式（T41）：必须为合法 hex 且恰为 AES-256 的 32 字节（64 位 hex）。
      校验委托 `common.security.crypto.aes_key_bytes` 单一事实源，避免非法值
      延迟到首次加解密才抛错（原缺陷：默认 `change_me` 非法 hex 运行时才报错）。
    """
    invalid = [
        name
        for name in ("JWT_SECRET", "AES_KEY")
        if str(getattr(cfg, name, "") or "").strip().lower() in _PLACEHOLDER_SECRETS
    ]
    if invalid:
        raise RuntimeError(
            "启动失败：以下密钥仍为默认占位值，必须由 KMS/密钥系统注入：" + ", ".join(invalid)
        )

    # T41：非占位值仍可能格式非法（如 "not-hex" / 长度不符），启动即失败。
    # 延迟导入：crypto 模块级依赖本文件，运行期导入不成环。
    from common.security.crypto import aes_key_bytes

    try:
        aes_key_bytes(str(getattr(cfg, "AES_KEY", "") or ""))
    except ValueError as exc:
        raise RuntimeError(f"启动失败：{exc}") from exc


settings = Settings()

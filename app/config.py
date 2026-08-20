"""配置中心：所有应用配置通过环境变量 / .env 注入（pydantic-settings）。

配置优先级：环境变量 > .env 文件 > 代码默认值。
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "rag-enterprise"
    app_env: str = "dev"  # dev | prod
    debug: bool = True

    # ---- 模型网关（OpenAI 兼容协议）----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.1

    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "bge-m3"

    # ---- 检索精排（OpenAI 兼容 /rerank；留空则跳过重排）----
    rerank_base_url: str = "https://api.siliconflow.cn/v1"
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # ---- 存储 ----
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "chunks"

    postgres_dsn: str = "postgresql+asyncpg://rag:ragpass@localhost:5432/rag"
    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "documents"

    # ---- 可观测（Langfuse）----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ---- 安全 ----
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_expire_minutes: int = 1440
    auth_disable_signup: bool = False  # 关闭开放注册（R1）：生产环境必须显式开启

    # ---- Agent（FR-17/19/23/24）----
    crag_min_score: float = 0.25  # rerank top1 低于此值触发查询重写
    crag_max_rewrites: int = 2  # 最多重写重检索轮数，超限降级使用现有结果
    query_rewrite_enabled: bool = True  # 检索前主动改写
    multi_query_enabled: bool = True  # 多查询扩展
    multi_query_count: int = 3  # 多查询变体数
    multi_hop_enabled: bool = True  # 多跳拆解（对比/兼容类问题）

    # ---- Agent 工具（FR-25，P2 W1）----
    agent_tools_enabled: bool = True  # plan_tools 节点开关
    tools_max_rows: int = 50  # 工具 SQL 查询结果行数上限

    # ---- Connector 定时摄入（FR-08，P2 W3）----
    connector_config: str = "config/connectors.json"  # 目录监控配置：[{path, department}]
    connector_interval: int = 60  # 常驻模式扫描间隔（秒）

    # ---- 敏感信息脱敏（FR-36，P2 W4）----
    redaction_enabled: bool = True  # 摄入与回答两环节的脱敏总开关
    redaction_config: str = "config/redaction.json"  # 自定义规则（追加到内置 PII 规则）

    # ---- 术语表/同义词（P2 W5）----
    term_expand_enabled: bool = True  # 检索前把别名变体并入查询集合
    term_config: str = "config/terms.json"  # {别名: 标准词}

    # ---- 数据清洗与质量门禁（P2 增强）----
    enable_llm_cleaning: bool = False  # LLM 清洗开关（默认关闭，规则清洗始终生效）
    llm_cleaning_min_len: int = 20  # 触发 LLM 清洗的最小块长（保护成本）

    @model_validator(mode="after")
    def _prod_requires_strong_secret(self) -> "Settings":
        """生产环境必须显式配置 ≥32 位随机 jwt_secret（默认值可伪造任意用户 token）。"""
        if self.app_env == "prod":
            if self.jwt_secret == "" or len(self.jwt_secret) < 32:
                raise ValueError("生产环境必须配置 ≥32 位的随机 jwt_secret（环境变量 JWT_SECRET）")
            if self.debug:
                raise ValueError("生产环境必须设置 DEBUG=false（500 错误栈会泄露内部信息）")
            if not self.auth_disable_signup:
                raise ValueError(
                    "生产环境必须设置 AUTH_DISABLE_SIGNUP=true（账号由管理员创建，关闭开放注册）"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

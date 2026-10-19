from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    bids_dir: Path = Field(default="sample_bids", env="BIDS_DIR")
    database_url: str = Field(env="DATABASE_URL", repr=False)
    max_candidates: int = Field(default=30, env="MAX_CANDIDATES")
    fuzzy_threshold: float = Field(default=0.3, env="FUZZY_THRESHOLD")
    outlier_multiple: int = Field(default=10, env="OUTLIER_MULTIPLE")
    min_comparables: int = Field(default=5, env="MIN_COMPARABLES")
    far_from_median_pct: float = Field(default=50, env="FAR_FROM_MEDIAN_PCT")
    max_scale_ratio: float = Field(default=10.0, env="MAX_SCALE_RATIO")
    high_confidence_n: int = Field(default=15, env="HIGH_CONFIDENCE_N")
    outputs_dir: Path = Field(default=Path(__file__).resolve().parent / "outputs", env="OUTPUTS_DIR")
    max_research_attempts: int = Field(default=3, env="MAX_RESEARCH_ATTEMPTS")
    max_draft_attempts: int = Field(default=2, env="MAX_DRAFT_ATTEMPTS")
    good_pool_n: int = Field(default=10, env="GOOD_POOL_N")
    judgement_batch_size: int = Field(default=8, env="JUDGEMENT_BATCH_SIZE")
    llm_provider: str = Field(default="groq", env="LLM_PROVIDER")
    llm_model: str = Field(default="", env="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, env="LLM_TEMPERATURE")
    llm_timeout_s: int = Field(default=120, env="LLM_TIMEOUT_S")
    llm_max_tokens: int = Field(default=16384, env="LLM_MAX_TOKENS")
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    groq_api_key: str = Field(default="", env="GROQ_API_KEY", repr=False)
    opik_enabled: bool = Field(default=True, env="OPIK_ENABLED")
    opik_api_key: str = Field(default="", env="OPIK_API_KEY", repr=False)
    opik_workspace: str = Field(default="", env="OPIK_WORKSPACE")
    opik_project: str = Field(default="value4money-agent", env="OPIK_PROJECT_NAME")
    opik_full_prompts: str = Field(default="research_next_move", env="OPIK_FULL_PROMPTS")
    community_mcp_enabled: bool = Field(default=True, env="COMMUNITY_MCP_ENABLED")
    community_mcp_command: str = Field(default="uvx", env="COMMUNITY_MCP_COMMAND")
    community_mcp_args: str = Field(default="--with mcp<2 postgres-mcp", env="COMMUNITY_MCP_ARGS")
    community_mcp_access_mode: str = Field(default="restricted", env="COMMUNITY_MCP_ACCESS_MODE")
    community_mcp_database_url: str = Field(default="", env="COMMUNITY_MCP_DATABASE_URL", repr=False)
    community_mcp_max_rows: int = Field(default=30, env="COMMUNITY_MCP_MAX_ROWS")
    audit_pool_size: int = Field(default=4, env="AUDIT_POOL_SIZE")

    # Web API
    api_token: str = Field(default="", env="API_TOKEN", repr=False)
    api_host: str = Field(default="127.0.0.1", env="API_HOST")
    api_port: int = Field(default=8000, env="API_PORT")
    cors_origins: str = Field(default="http://localhost:5173", env="CORS_ORIGINS")
    uploads_dir: Path = Field(default=Path(__file__).resolve().parent / "uploads", env="UPLOADS_DIR")
    run_max_concurrency: int = Field(default=2, env="RUN_MAX_CONCURRENCY")
    db_pool_size: int = Field(default=6, env="DB_POOL_SIZE")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

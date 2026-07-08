from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://ideascraper:ideascraper@localhost:5432/ideascraper"
    redis_url: str = "redis://localhost:6379"
    qdrant_url: str = "http://localhost:6333"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "papers"
    grobid_url: str = "http://localhost:8070"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    summary_model: str = "qwen3:8b"
    reasoning_model: str = "qwen3:8b"
    embed_model: str = "bge-large"
    embed_dim: int = 1024

    # cloud providers (both expose OpenAI-compatible endpoints); empty key = unavailable
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-5"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    search_max_results: int = 10
    prefilter_min_year: int = 0
    prefilter_min_citations: int = 0

    daily_token_budget: int = 2_000_000

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

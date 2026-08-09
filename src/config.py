from functools import lru_cache
from typing import Literal
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # LLM
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    mistral_api_key: str | None = os.getenv("MISTRAL_API_KEY")
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # LLM provider selection
    llm_provider: Literal["openai", "anthropic", "mistral"] = "mistral"

    # Set model based on provider
    model_name: str = "mistral-medium-3-5"

    # Rule Proposer tunables
    rule_proposer_concurrency: int = 10
    rule_proposer_max_retries: int = 2
    debug_dump_table_digests: bool = False

    # Database
    database_url: str = "sqlite:///./data/app.db"

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"


@lru_cache
def get_settings() -> Settings:
    return Settings()

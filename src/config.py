import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test", "local"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    # LLM
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    mistral_api_key: str | None = os.getenv("MISTRAL_API_KEY")
    google_api_key: str | None = os.getenv("GOOGLE_API_KEY")
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # LLM provider selection
    llm_provider: Literal["openai", "anthropic", "mistral", "google"] = os.getenv("PROVIDER") or "openai"

    # Set model based on provider
    # model_name: str = os.getenv("MISTRAL_MODEL") or "mistral-medium-latest"
    openai_model_name: str = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    anthropic_model_name: str = os.getenv("ANTHROPIC_MODEL") or "claude-opus-5"
    mistral_model_name: str = os.getenv("MISTRAL_MODEL") or "mistral-medium-latest"
    google_model_name: str = os.getenv("GOOGLE_MODEL") or "gemini-3.1-flash-lite"

    # Rule Proposer tunables
    rule_proposer_concurrency: int = 10
    rule_proposer_max_retries: int = 2
    debug_dump_table_digests: bool = False

    # Database
    database_url: str = Field(default="sqlite:///steward_local.db")

    # Vector Store
    chroma_persist_dir: str = "./data/chroma" # Change to .env later

    # Output
    output_dir: str = "./output"
    results_dir: str = "./output" # Backwards-compatible alias


@lru_cache
def get_settings() -> Settings:
    return Settings()

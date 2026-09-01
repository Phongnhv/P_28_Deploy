import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "DataPulse"
    app_env: Literal["development", "production", "test", "local"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"
    enable_public_demo: bool = False
    demo_steward_password: str | None = None
    rate_limit_hash_key: str | None = None
    trusted_proxy_cidrs: str = ""
    #: Số vòng PBKDF2 cho hash mật khẩu MỚI. Mặc định theo khuyến nghị OWASP
    #: cho PBKDF2-HMAC-SHA256. Hạ xuống CHỈ để chạy test: bộ test dựng lại
    #: database và seed lại tài khoản cho từng test, nên chi phí KDF thật cộng
    #: dồn thành hàng phút mà không kiểm chứng thêm được điều gì.
    password_hash_iterations: int = Field(default=600_000, ge=1_000)

    # LLM
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    mistral_api_key: str | None = os.getenv("MISTRAL_API_KEY")
    google_api_key: str | None = os.getenv("GOOGLE_API_KEY")
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    llm_request_timeout_seconds: int = Field(default=180, ge=5, le=180)

    # LLM provider selection
    llm_provider: Literal["openai", "anthropic", "mistral", "google"] = os.getenv("PROVIDER") or "openai"

    # Set model based on provider
    openai_model_name: str = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    anthropic_model_name: str = os.getenv("ANTHROPIC_MODEL") or "claude-opus-5"
    mistral_model_name: str = os.getenv("MISTRAL_MODEL") or "mistral-medium-latest"
    google_model_name: str = os.getenv("GOOGLE_MODEL") or "gemini-3.1-flash-lite"

    # Rule Proposer tunables
    agent_mode: Literal["mock", "graph"] = os.getenv("AGENT_MODE") or "mock"
    rule_proposer_concurrency: int = 2
    rule_proposer_max_retries: int = 2
    rule_proposer_batch_size: int = Field(default=20, ge=1, le=20)
    rule_proposer_mode: Literal["deepagent", "legacy"] = os.getenv("RULE_PROPOSER_MODE") or "deepagent"
    rule_proposer_max_tool_calls: int = 6
    rule_proposer_thread_tool_call_limit: int = Field(default=15, ge=1, le=200)
    # The one-shot proposer is a compatibility fallback for older dashboard
    # runs. Keep it available in local/test environments, but do not silently
    # switch production traffic away from the canonical agent path.
    rule_proposer_allow_legacy_fallback: bool = True
    debug_dump_table_digests: bool = False
    anomaly_investigation_mode: Literal["deepagent", "legacy"] = os.getenv("ANOMALY_INVESTIGATION_MODE") or "deepagent"
    anomaly_investigation_tool_call_limit: int = Field(default=10, ge=1, le=100)
    anomaly_investigation_thread_tool_call_limit: int = Field(default=20, ge=1, le=200)

    # Anomaly Detection versioning
    detector_config_version: str = os.getenv("DETECTOR_CONFIG_VERSION") or "anomaly-v2-iforest"

    # Database
    database_url: str = Field(
        default_factory=lambda: (
            os.getenv("DATABASE_URL")
            or os.getenv("SUPABASE_DATABASE_URL")
            or "sqlite:///steward_local.db"
        )
    )
    supabase_database_url: str | None = os.getenv("SUPABASE_DATABASE_URL")
    dq_execution_backend: Literal["auto", "local", "supabase"] = os.getenv("DQ_EXECUTION_BACKEND") or "auto"
    database_pool_size: int = Field(default=5, ge=1, le=20)
    database_max_overflow: int = Field(default=5, ge=0, le=20)
    database_pool_timeout_seconds: int = Field(default=30, ge=1, le=120)

    # Output
    output_dir: str = "./output"
    results_dir: str = "./output" # Backwards-compatible alias
    upload_dir: str = "./data/uploads"
    upload_max_bytes: int = Field(default=100 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    upload_max_rows: int = Field(default=1_000_000, ge=1, le=10_000_000)
    upload_max_columns: int = Field(default=128, ge=1, le=1024)
    upload_max_decoded_bytes: int = Field(default=512 * 1024 * 1024, ge=1024 * 1024)

    # Generated dbt artifacts (GCS/Cloud Run, AWS S3, or MinIO locally)
    object_storage_enabled: bool = True
    object_storage_provider: Literal["s3", "gcs"] = "s3"
    object_storage_bucket: str = "ridepulse-dbt-artifacts"
    object_storage_prefix: str = "dbt-tests"
    object_storage_region: str = "us-east-1"
    object_storage_endpoint_url: str | None = None
    object_storage_access_key_id: str | None = None
    object_storage_secret_access_key: str | None = None
    object_storage_max_attempts: int = Field(default=3, ge=1, le=10)
    object_storage_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    object_storage_read_timeout_seconds: int = Field(default=10, ge=1, le=120)

    @model_validator(mode="after")
    def disable_legacy_fallback_by_default_in_production(self):
        """Require an explicit opt-in before production uses compatibility code."""
        if (
            self.app_env == "production"
            and "rule_proposer_allow_legacy_fallback" not in self.model_fields_set
        ):
            self.rule_proposer_allow_legacy_fallback = False
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]

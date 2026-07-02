"""
Intelligence Operating System — Runtime Configuration
======================================================
All runtime configuration is driven by environment variables, validated and
typed via Pydantic BaseSettings.  A single ``Settings`` instance is created
at import time and exposed via ``get_settings()``.

Environment variables are loaded from:
  1. Shell environment
  2. ``backend/.env`` (local development)
  3. ``backend/.env.<ENVIRONMENT>`` (environment-specific override)

Never import raw ``os.environ`` anywhere else in the application.
Always use ``get_settings()`` or the FastAPI ``Depends(get_settings)`` pattern.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import (
    BCRYPT_ROUNDS,
    EMBEDDING_MODEL_DEFAULT,
    EMBEDDING_MODEL_DIMENSION_DEFAULT,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS,
    OLLAMA_MODEL_CODE,
    OLLAMA_MODEL_LARGE,
    OLLAMA_MODEL_MEDIUM,
    OLLAMA_MODEL_SMALL,
    OLLAMA_MODEL_VISION,
    OTEL_SERVICE_NAME,
    PAGINATION_DEFAULT_PAGE_SIZE,
    PAGINATION_MAX_PAGE_SIZE,
    RATE_LIMIT_DEFAULT_REQUESTS,
    RATE_LIMIT_DEFAULT_WINDOW_SECONDS,
    RERANKER_MODEL_DEFAULT,
    UPLOAD_MAX_SIZE_BYTES,
    UPLOAD_STORAGE_PATH,
)

# ---------------------------------------------------------------------------
# Base directory for relative path resolution
# ---------------------------------------------------------------------------
_BASE_DIR: Path = Path(__file__).resolve().parents[3]  # backend/


class _DatabaseSettings(BaseSettings):
    """Relational database (PostgreSQL) connection settings."""

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    host: str = Field(default="localhost", description="PostgreSQL host")
    port: int = Field(default=5432, description="PostgreSQL port")
    user: str = Field(default="ios_user", description="Database user")
    password: SecretStr = Field(description="Database password")
    db: str = Field(default="ios_db", description="Database name")

    # Connection pool
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=100)
    pool_timeout: float = Field(default=30.0, gt=0)
    pool_recycle: int = Field(
        default=1800, description="Seconds before recycling connections"
    )
    echo_sql: bool = Field(
        default=False, description="Log all SQL statements (dev only)"
    )

    @property
    def async_url(self) -> str:
        """Async DSN for asyncpg driver."""
        return (
            f"postgresql+asyncpg://{self.user}:"
            f"{self.password.get_secret_value()}@"
            f"{self.host}:{self.port}/{self.db}"
        )

    @property
    def sync_url(self) -> str:
        """Sync DSN for Alembic migrations (psycopg2 driver)."""
        return (
            f"postgresql+psycopg2://{self.user}:"
            f"{self.password.get_secret_value()}@"
            f"{self.host}:{self.port}/{self.db}"
        )


class _RedisSettings(BaseSettings):
    """Redis connection settings (cache, working memory, pub/sub)."""

    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    password: SecretStr | None = Field(default=None)
    db: int = Field(default=0, ge=0, le=15)
    ssl: bool = Field(default=False)
    max_connections: int = Field(default=50, ge=1)
    socket_timeout: float = Field(default=5.0)
    socket_connect_timeout: float = Field(default=5.0)

    @property
    def url(self) -> str:
        """Redis URL including auth if configured."""
        scheme = "rediss" if self.ssl else "redis"
        auth = f":{self.password.get_secret_value()}@" if self.password else ""
        return f"{scheme}://{auth}{self.host}:{self.port}/{self.db}"


class _QdrantSettings(BaseSettings):
    """Qdrant vector database connection settings."""

    model_config = SettingsConfigDict(env_prefix="QDRANT_", extra="ignore")

    host: str = Field(default="localhost")
    port: int = Field(default=6333)
    grpc_port: int = Field(default=6334)
    api_key: SecretStr | None = Field(default=None)
    prefer_grpc: bool = Field(default=False)
    https: bool = Field(default=False)
    timeout: float = Field(default=30.0)


class _Neo4jSettings(BaseSettings):
    """Neo4j graph database connection settings."""

    model_config = SettingsConfigDict(env_prefix="NEO4J_", extra="ignore")

    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: SecretStr = Field(description="Neo4j password")
    max_connection_pool_size: int = Field(default=50)
    connection_timeout: float = Field(default=30.0)
    connection_acquisition_timeout: float = Field(default=60.0)


class _OllamaSettings(BaseSettings):
    """Ollama local LLM server settings."""

    model_config = SettingsConfigDict(env_prefix="OLLAMA_", extra="ignore")

    base_url: AnyHttpUrl = Field(
        default="http://localhost:11434",  # type: ignore[assignment]
        description="Ollama REST API base URL",
    )
    timeout: float = Field(default=300.0, description="Request timeout in seconds")
    keep_alive: str = Field(default="5m", description="Model keep-alive duration")

    # Model assignments
    model_large: str = Field(default=OLLAMA_MODEL_LARGE)
    model_medium: str = Field(default=OLLAMA_MODEL_MEDIUM)
    model_code: str = Field(default=OLLAMA_MODEL_CODE)
    model_vision: str = Field(default=OLLAMA_MODEL_VISION)
    model_small: str = Field(default=OLLAMA_MODEL_SMALL)


class _MLflowSettings(BaseSettings):
    """MLflow experiment tracking settings."""

    model_config = SettingsConfigDict(env_prefix="MLFLOW_", extra="ignore")

    tracking_uri: str = Field(default="http://localhost:5000")
    default_experiment_name: str = Field(default="ios-experiments")
    artifact_root: str = Field(default="/data/mlflow/artifacts")


class _OTelSettings(BaseSettings):
    """OpenTelemetry collector settings."""

    model_config = SettingsConfigDict(env_prefix="OTEL_", extra="ignore")

    enabled: bool = Field(default=True)
    exporter_otlp_endpoint: str = Field(default="http://localhost:4317")
    service_name: str = Field(default=OTEL_SERVICE_NAME)
    service_version: str = Field(default="1.0.0")
    traces_sampler: str = Field(default="always_on")


class _JWTSettings(BaseSettings):
    """JWT token configuration."""

    model_config = SettingsConfigDict(env_prefix="JWT_", extra="ignore")

    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_hex(64)),
        description="HMAC secret; must be overridden in production via JWT_SECRET_KEY",
    )
    algorithm: str = Field(default=JWT_ALGORITHM)
    access_token_expire_minutes: int = Field(
        default=JWT_ACCESS_TOKEN_EXPIRE_MINUTES, ge=1
    )
    refresh_token_expire_days: int = Field(default=JWT_REFRESH_TOKEN_EXPIRE_DAYS, ge=1)


class _OAuthSettings(BaseSettings):
    """OAuth2 provider credentials."""

    model_config = SettingsConfigDict(env_prefix="OAUTH_", extra="ignore")

    google_client_id: str | None = Field(default=None)
    google_client_secret: SecretStr | None = Field(default=None)
    github_client_id: str | None = Field(default=None)
    github_client_secret: SecretStr | None = Field(default=None)

    # Redirect base URL (e.g., https://yourdomain.com)
    redirect_base_url: AnyHttpUrl | None = Field(default=None)  # type: ignore[assignment]


class _EmbeddingSettings(BaseSettings):
    """Embedding and reranking model configuration."""

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    model_name: str = Field(default=EMBEDDING_MODEL_DEFAULT)
    model_dimension: int = Field(default=EMBEDDING_MODEL_DIMENSION_DEFAULT)
    batch_size: int = Field(default=64, ge=1, le=512)
    device: str = Field(
        default="cpu",
        description="Torch device: 'cpu', 'cuda', 'mps'",
    )
    reranker_model: str = Field(default=RERANKER_MODEL_DEFAULT)
    cache_dir: str = Field(
        default="/data/models",
        description="HuggingFace model cache directory",
    )


class _RateLimitSettings(BaseSettings):
    """Rate limiting configuration."""

    model_config = SettingsConfigDict(env_prefix="RATE_LIMIT_", extra="ignore")

    enabled: bool = Field(default=True)
    default_requests: int = Field(default=RATE_LIMIT_DEFAULT_REQUESTS)
    default_window_seconds: int = Field(default=RATE_LIMIT_DEFAULT_WINDOW_SECONDS)


class Settings(BaseSettings):
    """
    Root application settings.

    Nested settings objects are composed here.  Sub-settings are loaded from
    their own prefixes automatically by Pydantic.
    """

    model_config = SettingsConfigDict(
        env_file=[
            str(_BASE_DIR / ".env"),
            str(_BASE_DIR / ".env.local"),
        ],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    environment: str = Field(
        default="development",
        description="Runtime environment: development | staging | production",
    )
    debug: bool = Field(default=False)
    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_hex(64)),
        description="Master application secret.  Override via SECRET_KEY env var.",
    )
    allowed_hosts: list[str] = Field(
        default=["*"],
        description="ALLOWED_HOSTS env var — comma-separated list",
    )
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed CORS origins — comma-separated list",
    )
    cors_allow_credentials: bool = Field(default=True)

    # ------------------------------------------------------------------
    # Server
    # ------------------------------------------------------------------
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, description="Uvicorn worker count")
    reload: bool = Field(
        default=False,
        description="Hot-reload (development only; incompatible with multiple workers)",
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Log level: DEBUG | INFO | WARNING | ERROR | CRITICAL",
    )
    log_format: str = Field(
        default="json",
        description="Log format: json | text",
    )
    log_file: str | None = Field(
        default=None,
        description="Optional file path for log output",
    )

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    default_page_size: int = Field(
        default=PAGINATION_DEFAULT_PAGE_SIZE,
        ge=1,
        le=PAGINATION_MAX_PAGE_SIZE,
    )
    max_page_size: int = Field(default=PAGINATION_MAX_PAGE_SIZE, ge=1)

    # ------------------------------------------------------------------
    # File upload
    # ------------------------------------------------------------------
    upload_max_size_bytes: int = Field(default=UPLOAD_MAX_SIZE_BYTES)
    upload_storage_path: str = Field(default=UPLOAD_STORAGE_PATH)

    # ------------------------------------------------------------------
    # Bcrypt
    # ------------------------------------------------------------------
    bcrypt_rounds: int = Field(default=BCRYPT_ROUNDS, ge=4, le=31)

    # ------------------------------------------------------------------
    # Sub-settings (composed via Pydantic's nested model loading)
    # ------------------------------------------------------------------
    db: _DatabaseSettings = Field(default_factory=_DatabaseSettings)  # type: ignore[call-arg]
    redis: _RedisSettings = Field(default_factory=_RedisSettings)  # type: ignore[call-arg]
    qdrant: _QdrantSettings = Field(default_factory=_QdrantSettings)  # type: ignore[call-arg]
    neo4j: _Neo4jSettings = Field(default_factory=_Neo4jSettings)  # type: ignore[call-arg]
    ollama: _OllamaSettings = Field(default_factory=_OllamaSettings)  # type: ignore[call-arg]
    mlflow: _MLflowSettings = Field(default_factory=_MLflowSettings)  # type: ignore[call-arg]
    otel: _OTelSettings = Field(default_factory=_OTelSettings)  # type: ignore[call-arg]
    jwt: _JWTSettings = Field(default_factory=_JWTSettings)  # type: ignore[call-arg]
    oauth: _OAuthSettings = Field(default_factory=_OAuthSettings)  # type: ignore[call-arg]
    embedding: _EmbeddingSettings = Field(default_factory=_EmbeddingSettings)  # type: ignore[call-arg]
    rate_limit: _RateLimitSettings = Field(default_factory=_RateLimitSettings)  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production", "testing"}
        if v.lower() not in allowed:
            raise ValueError(f"environment must be one of {allowed}, got '{v}'")
        return v.lower()

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return upper

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        """Accept either a Python list or a comma-separated env string."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [h.strip() for h in v.split(",") if h.strip()]
        return v

    @model_validator(mode="after")
    def production_safety_checks(self) -> "Settings":
        """Enforce non-negotiable constraints in production."""
        if self.environment == "production":
            if self.debug:
                raise ValueError("debug must be False in production")
            if self.reload:
                raise ValueError("reload must be False in production")
            if "*" in self.cors_origins:
                raise ValueError(
                    "Wildcard CORS origin is not permitted in production. "
                    "Set CORS_ORIGINS to your frontend domain(s)."
                )
        return self

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "testing"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Uses ``lru_cache`` so the environment is parsed only once per process.
    In tests, call ``get_settings.cache_clear()`` after patching env vars.
    """
    return Settings()  # type: ignore[call-arg]

"""Environment-driven configuration, validated once at process start.

Design rule: **a service must refuse to boot if it is misconfigured.** A
missing `JWT_SECRET_KEY` should crash the container immediately and loudly, not
surface three days later as a mysterious 500 on a checkout page. Pydantic
Settings gives us that for free — every field below is type-checked and
range-checked at import time.

Each service subclasses `ServiceSettings` to add its own fields:

    class AuthSettings(ServiceSettings):
        service_name: str = "auth"
        db_schema: str = "auth"
        bootstrap_admin_email: EmailStr | None = None
"""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]


class ServiceSettings(BaseSettings):
    """Configuration common to every microservice.

    Values are read from environment variables (case-insensitively), falling
    back to a local `.env` file when one is present. In Docker the variables are
    injected by compose, so the `.env` fallback only matters when you run a
    service directly on your machine.

    Attributes:
        service_name: Short identifier, e.g. ``"auth"``. Appears in every log
            line and in the ``/health`` payload. Subclasses override this.
        db_schema: The Postgres schema this service owns. The service is granted
            access to this schema and no other — see
            ``docker/postgres/010-init-schemas.sql``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the shared .env holds keys for all services; ignore foreign ones
    )

    # --- Identity of this service -------------------------------------------
    service_name: str = "service"
    db_schema: str = "public"

    # --- Runtime ------------------------------------------------------------
    environment: Environment = "development"
    log_level: str = "INFO"

    # --- Postgres -----------------------------------------------------------
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "ecom"
    postgres_user: str = "ecom"
    postgres_password: SecretStr = SecretStr("")

    # --- Tokens -------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3600)

    # --- HTTP ---------------------------------------------------------------
    cors_allow_origins: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8080"
    public_web_url: str = "http://localhost:3000"

    @field_validator("log_level")
    @classmethod
    def _uppercase_log_level(cls, value: str) -> str:
        """Accept ``debug`` or ``DEBUG``; the stdlib only accepts the latter."""
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return level

    @property
    def is_production(self) -> bool:
        """True when running in a deployed environment.

        Gates every "safe in dev, dangerous in prod" behaviour in one place:
        interactive API docs, stack traces in responses, and permissive CORS.
        """
        return self.environment == "production"

    @property
    def cors_origins(self) -> list[str]:
        """`CORS_ALLOW_ORIGINS` split into a list, blanks and slashes trimmed."""
        return [o.strip().rstrip("/") for o in self.cors_allow_origins.split(",") if o.strip()]

    @cached_property
    def database_url(self) -> str:
        """Async SQLAlchemy DSN for this service's Postgres connection.

        Uses the ``asyncpg`` driver. Cached because building it repeatedly would
        unwrap the ``SecretStr`` password on every call, and the fewer places
        that happens the better.
        """
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """Blocking DSN using ``psycopg``, required by Alembic.

        Alembic's migration runner is synchronous. Rather than fight it with an
        async adapter, each service runs migrations over a plain connection and
        serves traffic over the async one.
        """
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def require_secrets(self) -> None:
        """Fail fast if a production-critical secret is missing or a dev default.

        Called from `create_service_app()` during startup. In development a weak
        secret only logs a warning so `docker compose up` works out of the box;
        in production it raises and the container exits.

        Raises:
            RuntimeError: In production, when a required secret is unset or too
                short to be safe.
        """
        problems: list[str] = []

        if len(self.jwt_secret_key.get_secret_value()) < 32:
            problems.append("JWT_SECRET_KEY must be at least 32 characters")
        if not self.postgres_password.get_secret_value():
            problems.append("POSTGRES_PASSWORD must not be empty")

        if not problems:
            return
        if self.is_production:
            raise RuntimeError(
                "Refusing to start in production with insecure configuration: "
                + "; ".join(problems)
                + ". Run `make secrets` to generate strong values."
            )
        # Development: warn but keep going so the stack is easy to spin up.
        import warnings

        warnings.warn(
            "Insecure development configuration: " + "; ".join(problems),
            RuntimeWarning,
            stacklevel=2,
        )

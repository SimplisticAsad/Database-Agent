"""Environment-driven configuration. Secrets are never printed or logged."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = Field(default=16000, ge=256)
    llm_timeout_seconds: float = Field(default=300.0, gt=0)
    anthropic_api_key: SecretStr | None = None

    database_url: SecretStr = SecretStr(
        "postgresql://agent:agent_dev_only@localhost:5432/database_agent"
    )
    target_schema: str | None = None
    statement_timeout_ms: int = Field(default=30_000, ge=100)

    max_retries: int = Field(default=3, ge=1, description="Total attempts per stage")
    max_test_repair_rounds: int = Field(default=2, ge=0)

    generated_dir: Path = Path("generated")
    logs_dir: Path = Path("logs")
    prompts_dir: Path = APP_DIR / "prompts"

    def secret_values(self) -> list[str]:
        """Every secret string that must be scrubbed from logs."""
        secrets = [self.database_url.get_secret_value()]
        password = urlsplit(secrets[0]).password
        if password:
            secrets.append(password)
        if self.anthropic_api_key:
            secrets.append(self.anthropic_api_key.get_secret_value())
        return [s for s in secrets if s]

    def redacted_database_url(self) -> str:
        parts = urlsplit(self.database_url.get_secret_value())
        if parts.password:
            netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
            parts = parts._replace(netloc=netloc)
        return urlunsplit(parts)


@lru_cache
def get_settings() -> Settings:
    return Settings()

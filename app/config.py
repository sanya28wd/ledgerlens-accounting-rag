from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration; defaults are explicit deployment policy."""

    model_config = SettingsConfigDict(env_file=".env", extra="forbid")
    openai_api_key: SecretStr = SecretStr("")
    reviewer_key: SecretStr = Field(min_length=16)
    chat_model: str = "gpt-6-luna"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1, le=4096)
    data_dir: Path = Path("data")
    allowed_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]
    max_file_bytes: int = 10 * 1024 * 1024
    max_pages: int = 25
    max_context_tokens: int = 24000
    extraction_timeout: int = 180
    tessdata_prefix: str | None = None


def load_settings() -> Settings:
    return Settings()

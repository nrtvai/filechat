from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path(".filechat"), validation_alias="FILECHAT_DATA_DIR")
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    filechat_chat_model: str = Field(default="openai/gpt-4o-mini", validation_alias="FILECHAT_CHAT_MODEL")
    filechat_embedding_model: str = Field(default="openai/text-embedding-3-small", validation_alias="FILECHAT_EMBEDDING_MODEL")
    filechat_ocr_model: str = Field(default="openai/gpt-4o-mini", validation_alias="FILECHAT_OCR_MODEL")
    filechat_allow_fake_openrouter: bool = Field(default=False, validation_alias="FILECHAT_ALLOW_FAKE_OPENROUTER")

    @property
    def resolved_data_dir(self) -> Path:
        root = self.data_dir.expanduser()
        root.mkdir(parents=True, exist_ok=True)
        (root / "uploads").mkdir(parents=True, exist_ok=True)
        (root / "artifacts").mkdir(parents=True, exist_ok=True)
        return root


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()

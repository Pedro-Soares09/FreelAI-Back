from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Perfil de execução: "producao" usa prazos reais; "demo" usa prazos em minutos
    # para permitir apresentações curtas.
    perfil: Literal["producao", "demo"] = "producao"
    database_url: str = "sqlite:///./freelai.db"
    secret_key: str = "change-this-secret-key"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()

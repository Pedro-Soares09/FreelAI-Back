from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SEGREDO_PADRAO = "change-this-secret-key"


class Settings(BaseSettings):
    # Perfil de execução: "producao" usa prazos reais; "demo" usa prazos em minutos
    # para permitir apresentações curtas.
    perfil: Literal["producao", "demo"] = "producao"
    # Ambiente de implantação. Em "producao" a aplicação se recusa a iniciar com segredo fraco.
    ambiente: Literal["desenvolvimento", "producao"] = "desenvolvimento"
    database_url: str = "sqlite:///./freelai.db"
    secret_key: str = SEGREDO_PADRAO
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_minutes: int = 60 * 24 * 7
    cors_origins: list[str] = ["http://localhost:3000"]
    uploads_dir: str = "uploads"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def _exigir_segredo_forte_em_producao(self) -> "Settings":
        segredo_de_exemplo = self.secret_key in (SEGREDO_PADRAO,) or self.secret_key.startswith(
            "replace-this"
        )
        if self.ambiente == "producao" and (segredo_de_exemplo or len(self.secret_key) < 32):
            raise ValueError(
                "SECRET_KEY precisa ser definida com pelo menos 32 caracteres em produção."
            )
        return self


settings = Settings()

from typing import Literal

from pydantic import field_validator, model_validator
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
    # Procura bairros desconhecidos no OpenStreetMap (Nominatim); desligado por padrão.
    geocodificacao_osm: bool = False
    # Agendador das varreduras de prazo; 0 no intervalo usa o padrão do perfil (30 s demo, 60 s).
    varreduras_automaticas: bool = True
    varreduras_intervalo_s: int = 0
    # Limite de tentativas nas rotas de login, registro e contestação; desligável em testes.
    limite_credenciais_ativo: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parsear_cors_origins(cls, valor: object) -> object:
        if isinstance(valor, str):
            texto = valor.strip()
            if not texto:
                return ["http://localhost:3000"]
            if not texto.startswith("["):
                return [item.strip() for item in texto.split(",") if item.strip()]
        return valor

    @model_validator(mode="after")
    def _normalizar_url_do_banco(self) -> "Settings":
        # Hospedagens entregam "postgres://" ou "postgresql://"; o driver instalado é o psycopg 3.
        for prefixo in ("postgres://", "postgresql://"):
            if self.database_url.startswith(prefixo):
                self.database_url = "postgresql+psycopg://" + self.database_url[len(prefixo) :]
        return self

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

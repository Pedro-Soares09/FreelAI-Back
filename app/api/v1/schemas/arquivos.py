"""Schemas de resposta do módulo de upload de arquivos."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import TipoArquivo


class ArquivoEnviado(BaseModel):
    """Metadados e URL do arquivo armazenado após o upload."""

    id: int
    tipo: TipoArquivo
    url: str = Field(description="URL da API para acessar o arquivo armazenado.")
    mime_type: str
    tamanho_bytes: int
    criado_em: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 12,
                    "tipo": "foto",
                    "url": "/api/v1/arquivos/foto/a1b2c3d4e5f60718293a4b5c6d7e8f90.jpg",
                    "mime_type": "image/jpeg",
                    "tamanho_bytes": 48210,
                    "criado_em": "2026-11-20T14:00:00Z",
                }
            ]
        }
    )

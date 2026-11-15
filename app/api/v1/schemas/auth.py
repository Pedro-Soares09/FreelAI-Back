from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import EstadoConta, PapelUsuario, TipoPerfil


class RegistroEntrada(BaseModel):
    nome: str = Field(min_length=1, max_length=120, examples=["Ana Souza"])
    email: EmailStr = Field(examples=["ana@example.com"])
    senha: str = Field(min_length=8, max_length=72, examples=["senha-segura-123"])


class LoginEntrada(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=72)


class RenovacaoEntrada(BaseModel):
    refresh_token: str


class PerfilAtivoEntrada(BaseModel):
    perfil: TipoPerfil


class ParDeTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expira_em_segundos: int = Field(description="Validade do token de acesso, em segundos.")


class UsuarioAtual(BaseModel):
    """Conta autenticada e seus perfis."""

    id: int
    nome: str
    email: EmailStr
    estado: EstadoConta
    papel: PapelUsuario
    perfil_ativo: TipoPerfil | None = Field(
        description="Perfil usado nas ações atuais; nulo até o primeiro perfil ser cadastrado."
    )
    perfis: list[TipoPerfil] = Field(description="Perfis já cadastrados nesta conta.")
    verificado: bool = Field(description="Identidade do perfil ativo aprovada pela administração.")
    suspensa_ate: datetime | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 7,
                    "nome": "Ana Souza",
                    "email": "ana@example.com",
                    "estado": "ativa",
                    "papel": "usuario",
                    "perfil_ativo": "profissional",
                    "perfis": ["profissional", "contratante"],
                    "verificado": True,
                    "suspensa_ate": None,
                }
            ]
        }
    )

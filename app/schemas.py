from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: EmailStr
    senha: str = Field(min_length=8, max_length=72)
    tipo: str = Field(default="freelancer", min_length=1, max_length=30)

    @field_validator("nome")
    @classmethod
    def _nome_sem_espacos_nas_pontas(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("O nome não pode ficar em branco.")
        return valor

    @field_validator("senha")
    @classmethod
    def _senha_cabe_no_limite_do_hash(cls, valor: str) -> str:
        if len(valor.encode("utf-8")) > 72:
            raise ValueError("A senha deve ter no máximo 72 bytes em UTF-8.")
        return valor


class UserLogin(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=72)


class UserPublic(BaseModel):
    id: int
    nome: str
    email: EmailStr
    tipo: str
    verificado: bool

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    email: EmailStr
    senha: str = Field(min_length=8, max_length=72)
    tipo: str = Field(default="freelancer", min_length=1, max_length=30)


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

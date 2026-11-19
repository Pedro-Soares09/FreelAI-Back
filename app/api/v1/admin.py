from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import exige_admin
from app.models import Denuncia, EstadoDenuncia, OrigemDenuncia
from app.services import configuracao, varreduras
from app.tempo import como_utc

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])


class ResultadoVarredura(BaseModel):
    executado_em: str
    alteracoes: dict[str, int] = Field(description="Registros alterados por varredura.")


class ConfiguracoesEfetivas(BaseModel):
    perfil: str
    valores: dict[str, float]


class DenunciaAdminResumo(BaseModel):
    """Denúncia (manual ou automática) exibida na fila da administração."""

    id: int
    denunciante_id: int | None = None
    denunciado_id: int
    vaga_id: int | None = None
    conversa_id: int | None = None
    origem: OrigemDenuncia
    motivo: str
    descricao: str | None = None
    estado: EstadoDenuncia
    decisao_motivo: str | None = None
    criado_em: datetime
    decidido_em: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "denunciante_id": None,
                    "denunciado_id": 7,
                    "vaga_id": 42,
                    "conversa_id": 9,
                    "origem": "automatica",
                    "motivo": "contato_externo_no_chat",
                    "descricao": (
                        "Usuário tentou compartilhar telefone ou WhatsApp 3 vezes na conversa 9."
                    ),
                    "estado": "aberta",
                    "decisao_motivo": None,
                    "criado_em": "2026-11-20T14:15:00Z",
                    "decidido_em": None,
                }
            ]
        },
    )


@router.post("/prazos/varrer", response_model=ResultadoVarredura)
def varrer_prazos(db: Session = Depends(get_db)) -> dict:
    """Força a verificação imediata de tudo que depende de prazo (expirações e liberações)."""
    return varreduras.executar_varreduras(db)


@router.get("/configuracoes", response_model=ConfiguracoesEfetivas)
def consultar_configuracoes(db: Session = Depends(get_db)) -> ConfiguracoesEfetivas:
    """Prazos, pesos e limites em vigor para o perfil de execução atual."""
    return ConfiguracoesEfetivas(perfil=configuracao.perfil_atual(), valores=configuracao.todas(db))


@router.get("/denuncias", response_model=list[DenunciaAdminResumo])
def listar_denuncias(
    estado: EstadoDenuncia | None = Query(default=None),
    origem: OrigemDenuncia | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[DenunciaAdminResumo]:
    """Lista as denúncias (incluindo as automáticas abertas pelo bloqueio do chat)."""
    consulta = select(Denuncia).order_by(Denuncia.id.desc())
    if estado is not None:
        consulta = consulta.where(Denuncia.estado == estado)
    if origem is not None:
        consulta = consulta.where(Denuncia.origem == origem)
    itens = db.scalars(consulta).all()
    return [
        DenunciaAdminResumo(
            id=d.id,
            denunciante_id=d.denunciante_id,
            denunciado_id=d.denunciado_id,
            vaga_id=d.vaga_id,
            conversa_id=d.conversa_id,
            origem=d.origem,
            motivo=d.motivo,
            descricao=d.descricao,
            estado=d.estado,
            decisao_motivo=d.decisao_motivo,
            criado_em=como_utc(d.criado_em),
            decidido_em=como_utc(d.decidido_em) if d.decidido_em is not None else None,
        )
        for d in itens
    ]


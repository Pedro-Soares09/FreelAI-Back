from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.schemas.denuncias import DecisaoDenunciaEntrada, DenunciaFilaAdmin
from app.api.v1.schemas.disputas import DecisaoDisputaEntrada, DisputaAdminDetalhe
from app.db import get_db
from app.deps import exige_admin
from app.models import EstadoDenuncia, EstadoDisputa, OrigemDenuncia
from app.services import configuracao, denuncias, disputas, varreduras

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(exige_admin)])


class ResultadoVarredura(BaseModel):
    executado_em: str
    alteracoes: dict[str, int] = Field(description="Registros alterados por varredura.")


class ConfiguracoesEfetivas(BaseModel):
    perfil: str
    valores: dict[str, float]


@router.post("/prazos/varrer", response_model=ResultadoVarredura)
def varrer_prazos(db: Session = Depends(get_db)) -> dict:
    """Força a verificação imediata de tudo que depende de prazo (expirações e liberações)."""
    return varreduras.executar_varreduras(db)


@router.get("/configuracoes", response_model=ConfiguracoesEfetivas)
def consultar_configuracoes(db: Session = Depends(get_db)) -> ConfiguracoesEfetivas:
    """Prazos, pesos e limites em vigor para o perfil de execução atual."""
    return ConfiguracoesEfetivas(perfil=configuracao.perfil_atual(), valores=configuracao.todas(db))


@router.get("/disputas", response_model=list[DisputaAdminDetalhe])
def listar_disputas(
    estado: EstadoDisputa | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[DisputaAdminDetalhe]:
    """Aba de disputas do admin: lista as contestações com o chat e os anexos de cada vaga."""
    return disputas.listar_disputas_admin(db, estado=estado)


@router.get("/disputas/{disputa_id}", response_model=DisputaAdminDetalhe)
def detalhar_disputa_admin(
    disputa_id: int,
    db: Session = Depends(get_db),
) -> DisputaAdminDetalhe:
    """Detalhe de uma disputa na aba do admin, com histórico do chat, anexos e pagamento."""
    return disputas.consultar_disputa_admin(db, disputa_id)


@router.post("/disputas/{disputa_id}/decisao", response_model=DisputaAdminDetalhe)
def decidir_disputa(
    disputa_id: int,
    entrada: DecisaoDisputaEntrada,
    db: Session = Depends(get_db),
) -> DisputaAdminDetalhe:
    """Decide a disputa (reembolso total, parcial ou pagamento integral) e gera os lançamentos."""
    return disputas.decidir_disputa(db, disputa_id, entrada)


@router.get("/denuncias", response_model=list[DenunciaFilaAdmin])
def listar_fila_de_denuncias(
    estado: EstadoDenuncia | None = Query(default=None),
    origem: OrigemDenuncia | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[DenunciaFilaAdmin]:
    """Fila de denúncias (manuais e automáticas) com tempo em espera e meta de 48h úteis."""
    return denuncias.listar_fila_admin(db, estado=estado, origem=origem)


@router.post("/denuncias/{denuncia_id}/decisao", response_model=DenunciaFilaAdmin)
def decidir_denuncia(
    denuncia_id: int,
    entrada: DecisaoDenunciaEntrada,
    db: Session = Depends(get_db),
) -> DenunciaFilaAdmin:
    """Decide uma denúncia da fila como procedente ou improcedente, registrando o motivo."""
    return denuncias.decidir_denuncia(
        db,
        denuncia_id,
        decisao=entrada.decisao,
        motivo=entrada.motivo,
    )

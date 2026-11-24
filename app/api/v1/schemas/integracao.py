"""Schemas compartilhados entre telas: perfil resumido, vaga, conversa, acordo e mapa."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import EstadoVaga


class PerfilResumo(BaseModel):
    """Dados públicos de um perfil, exibidos em cards, listas e no chat."""

    usuario_id: int
    nome: str
    foto_url: str | None = None
    verificado: bool = Field(description="Identidade aprovada pela administração.")
    novo: bool = Field(description="Ainda exibe o selo Novo.")
    pro: bool = Field(description="Assinante do plano Pro.")
    nota_media: float | None = Field(default=None, description="Média das avaliações, de 1 a 5.")
    total_avaliacoes: int = 0
    adicionais_pagos: int = Field(
        default=0, description="Horas extras e bônus pagos (só contratantes)."
    )
    destaque: bool = Field(
        default=False, description="Contratante com adicionais pagos; sem efeito no match."
    )


class VagaResumo(BaseModel):
    """Vaga como aparece em cards e no detalhe. O endereço completo só vai ao contratado."""

    id: int
    titulo: str
    categoria: str
    estado: EstadoVaga
    valor_inicial: Decimal
    valor_fixado: Decimal | None = None
    inicio_em: datetime
    duracao_minutos: int
    bairro: str
    distancia_km: float | None = Field(
        default=None, description="Distância até quem consulta, quando há localização."
    )
    contratante: PerfilResumo

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "titulo": "Montagem de guarda-roupa",
                    "categoria": "montagem-de-moveis",
                    "estado": "publicada",
                    "valor_inicial": "150.00",
                    "valor_fixado": None,
                    "inicio_em": "2026-11-20T14:00:00Z",
                    "duracao_minutos": 180,
                    "bairro": "Várzea",
                    "distancia_km": 1.2,
                    "contratante": {
                        "usuario_id": 3,
                        "nome": "Bia Lima",
                        "foto_url": None,
                        "verificado": True,
                        "novo": False,
                        "pro": False,
                        "nota_media": 4.8,
                        "total_avaliacoes": 12,
                    },
                }
            ]
        }
    )


class MensagemResumo(BaseModel):
    autor_id: int
    texto: str | None
    enviada_em: datetime


class ConversaResumo(BaseModel):
    """Conversa ligada a uma vaga. Só existe depois que o convite é aceito."""

    id: int
    vaga_id: int
    aberta: bool = Field(description="Falso quando a vaga foi cancelada ou encerrada.")
    bloqueada: bool = Field(
        default=False,
        description="Verdadeiro quando uma denúncia procedente bloqueou o envio de mensagens.",
    )
    outra_parte: PerfilResumo
    ultima_mensagem: MensagemResumo | None = None
    nao_lidas: int = 0

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 9,
                    "vaga_id": 42,
                    "aberta": True,
                    "outra_parte": {
                        "usuario_id": 7,
                        "nome": "Ana Souza",
                        "foto_url": None,
                        "verificado": True,
                        "novo": True,
                        "pro": False,
                        "nota_media": None,
                        "total_avaliacoes": 0,
                    },
                    "ultima_mensagem": {
                        "autor_id": 7,
                        "texto": "Chego às 14h.",
                        "enviada_em": "2026-11-19T18:30:00Z",
                    },
                    "nao_lidas": 1,
                }
            ]
        }
    )


class AceitesAcordo(BaseModel):
    profissional: bool
    contratante: bool


class DadosAcordo(BaseModel):
    """Resumo do acordo: o que chega ao pagamento e ao botão Li e aceito."""

    vaga_id: int
    servico: str
    escopo: str
    inicio_em: datetime
    duracao_minutos: int
    vestimenta: str | None = None
    atribuicoes: str | None = None
    local_bairro: str
    endereco_completo: str | None = Field(
        default=None, description="Preenchido somente para o profissional contratado."
    )
    valor_total: Decimal
    taxa_plataforma: Decimal
    valor_liquido_profissional: Decimal
    condicoes_pagamento: str
    regras_cancelamento: str
    condicoes_avaliacao: str
    versao_termos: str
    profissional: PerfilResumo
    contratante: PerfilResumo
    aceites: AceitesAcordo

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "vaga_id": 42,
                    "servico": "Montagem de guarda-roupa",
                    "escopo": "Montar guarda-roupa de 6 portas, com fixação na parede.",
                    "inicio_em": "2026-11-20T14:00:00Z",
                    "duracao_minutos": 180,
                    "vestimenta": "Camisa e calça",
                    "atribuicoes": "Levar as próprias ferramentas.",
                    "local_bairro": "Várzea",
                    "endereco_completo": None,
                    "valor_total": "150.00",
                    "taxa_plataforma": "15.00",
                    "valor_liquido_profissional": "135.00",
                    "condicoes_pagamento": "Pix retido e liberado após a conclusão.",
                    "regras_cancelamento": "Cancelamento com menos de 4h gera advertência.",
                    "condicoes_avaliacao": "Avaliação mútua de 1 a 5 em até 48h.",
                    "versao_termos": "1.0",
                    "profissional": {
                        "usuario_id": 7,
                        "nome": "Ana Souza",
                        "foto_url": None,
                        "verificado": True,
                        "novo": True,
                        "pro": False,
                        "nota_media": None,
                        "total_avaliacoes": 0,
                    },
                    "contratante": {
                        "usuario_id": 3,
                        "nome": "Bia Lima",
                        "foto_url": None,
                        "verificado": True,
                        "novo": False,
                        "pro": False,
                        "nota_media": 4.8,
                        "total_avaliacoes": 12,
                    },
                    "aceites": {"profissional": True, "contratante": False},
                }
            ]
        }
    )


class PontoMapa(BaseModel):
    """Ponto genérico do mapa: serve a vagas e a profissionais."""

    id: int
    tipo: Literal["vaga", "profissional"]
    titulo: str
    latitude: float
    longitude: float
    bairro: str
    distancia_km: float | None = None
    valor: Decimal | None = Field(default=None, description="Valor da vaga, quando for uma vaga.")
    categoria: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": 42,
                    "tipo": "vaga",
                    "titulo": "Montagem de guarda-roupa",
                    "latitude": -8.0505,
                    "longitude": -34.95,
                    "bairro": "Várzea",
                    "distancia_km": 1.2,
                    "valor": "150.00",
                    "categoria": "montagem-de-moveis",
                }
            ]
        }
    )

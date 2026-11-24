"""Parâmetros ajustáveis da plataforma (prazos, pesos e limites) por perfil de execução.

Nenhum prazo ou peso deve ser fixado no código das regras: tudo é lido daqui. Cada chave tem um
valor padrão para o perfil `producao` e outro para o perfil `demo`, que reduz horas e dias para
minutos. Linhas na tabela `configuracoes` sobrepõem os padrões.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Configuracao

Perfil = Literal["producao", "demo"]


@dataclass(frozen=True)
class Parametro:
    producao: float
    demo: float
    descricao: str
    inteiro: bool = True

    def padrao(self, perfil: Perfil) -> float:
        return self.producao if perfil == "producao" else self.demo


# Tempos em minutos, distâncias em km, percentuais de 0 a 100 e pesos de 0 a 1.
PARAMETROS: dict[str, Parametro] = {
    "janela_aceite_min": Parametro(30, 2, "Prazo para o profissional escolhido aceitar o convite"),
    "prazo_pagamento_min": Parametro(
        15, 2, "Prazo para o contratante pagar após o aceite do contrato"
    ),
    "expiracao_vaga_min": Parametro(720, 5, "Tempo de uma vaga publicada sem contratação"),
    "inicio_minimo_vaga_min": Parametro(60, 1, "Antecedência mínima do início de uma vaga"),
    "confirmacao_conclusao_min": Parametro(
        1440, 3, "Prazo após o fim do serviço para o contratante confirmar a conclusão"
    ),
    "liberacao_parcial_min": Parametro(
        4320, 6, "Prazo após o fim do serviço para liberar 50% quando não há validação facial"
    ),
    "avaliacao_prazo_min": Parametro(2880, 3, "Prazo para avaliar após a conclusão"),
    "cancelamento_tardio_min": Parametro(
        240, 1, "Cancelar com menos que isso de antecedência gera advertência"
    ),
    "tolerancia_falta_min": Parametro(30, 1, "Atraso após o início que permite registrar falta"),
    "checkin_antecedencia_min": Parametro(
        30, 30, "Antecedência a partir da qual o check-in é aceito"
    ),
    "checkin_raio_m": Parametro(300, 300, "Distância máxima do local para aceitar o check-in"),
    "disponivel_agora_min": Parametro(240, 3, "Duração do modo Disponível agora"),
    "notificacao_atraso_min": Parametro(2, 0, "Atraso das notificações para quem não é Pro"),
    "contraproposta_max_pct": Parametro(50, 50, "Acréscimo máximo de uma contraproposta"),
    "taxa_plataforma_pct": Parametro(
        10, 10, "Taxa da plataforma sobre o valor do serviço", inteiro=False
    ),
    "liberacao_parcial_pct": Parametro(
        50,
        50,
        "Parcela liberada ao profissional quando não há validação facial no prazo",
        inteiro=False,
    ),
    "peso_localizacao": Parametro(0.40, 0.40, "Peso da localização no match", inteiro=False),
    "peso_habilidades": Parametro(0.30, 0.30, "Peso das habilidades no match", inteiro=False),
    "peso_reputacao": Parametro(0.20, 0.20, "Peso da reputação no match", inteiro=False),
    "peso_preco": Parametro(0.10, 0.10, "Peso do preço no match", inteiro=False),
    "plano_pro_valor": Parametro(
        29.90, 29.90, "Valor mensal do plano Pro (simulado)", inteiro=False
    ),
    "plano_pro_dias": Parametro(30, 1, "Duração de cada período do plano Pro, em dias"),
    "raio_padrao_km": Parametro(5, 5, "Raio de busca padrão do profissional"),
    "raio_maximo_km": Parametro(15, 15, "Raio de busca máximo do profissional"),
    "servicos_para_deixar_de_ser_novo": Parametro(5, 5, "Serviços concluídos até o selo Novo sair"),
    "advertencias_para_suspensao": Parametro(3, 3, "Advertências no período que suspendem a conta"),
    "periodo_advertencias_dias": Parametro(30, 30, "Janela de contagem das advertências"),
    "suspensao_advertencias_dias": Parametro(7, 7, "Duração da suspensão por advertências"),
    "denuncias_para_suspensao": Parametro(3, 3, "Denúncias procedentes que suspendem a conta"),
    "suspensao_denuncias_dias": Parametro(30, 30, "Duração da suspensão por denúncias"),
    "anexo_max_mb": Parametro(10, 10, "Tamanho máximo de um arquivo enviado"),
}


class ChaveDesconhecida(KeyError):
    """A chave de configuração solicitada não existe."""


def perfil_atual() -> Perfil:
    return settings.perfil


def _converter(parametro: Parametro, valor: float | str) -> float:
    numero = float(valor)
    return int(numero) if parametro.inteiro else numero


def get(chave: str, db: Session | None = None, perfil: Perfil | None = None) -> float:
    """Valor da configuração `chave` para o perfil ativo.

    Sem `db`, devolve o padrão do perfil. Com `db`, linhas da tabela `configuracoes` para o
    perfil têm prioridade sobre o padrão.
    """
    try:
        parametro = PARAMETROS[chave]
    except KeyError:
        raise ChaveDesconhecida(chave) from None
    perfil = perfil or perfil_atual()
    if db is not None:
        valor = db.scalar(
            select(Configuracao.valor).where(
                Configuracao.chave == chave, Configuracao.perfil == perfil
            )
        )
        if valor is not None:
            return _converter(parametro, valor)
    return _converter(parametro, parametro.padrao(perfil))


def todas(db: Session | None = None, perfil: Perfil | None = None) -> dict[str, float]:
    """Todas as configurações efetivas para o perfil."""
    return {chave: get(chave, db, perfil) for chave in PARAMETROS}


def semear_configuracoes(db: Session) -> int:
    """Grava os padrões dos dois perfis que ainda não existem. Idempotente.

    Retorna a quantidade de linhas criadas.
    """
    existentes = set(db.execute(select(Configuracao.chave, Configuracao.perfil)).all())
    criadas = 0
    for chave, parametro in PARAMETROS.items():
        for perfil in ("producao", "demo"):
            if (chave, perfil) in existentes:
                continue
            db.add(
                Configuracao(
                    chave=chave,
                    perfil=perfil,
                    valor=str(parametro.padrao(perfil)),
                    descricao=parametro.descricao,
                )
            )
            criadas += 1
    db.commit()
    return criadas

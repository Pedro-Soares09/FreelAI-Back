"""Registro das varreduras periódicas (expirações, liberações automáticas e similares).

Cada módulo registra suas funções com `@varredura("nome")`. Uma varredura recebe a sessão do
banco e devolve quantos registros alterou. O endpoint administrativo e qualquer agendador
chamam `executar_varreduras` para aplicar todas de uma vez.
"""

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.tempo import agora

FuncaoVarredura = Callable[[Session], int]

_REGISTRO: dict[str, FuncaoVarredura] = {}


def varredura(nome: str) -> Callable[[FuncaoVarredura], FuncaoVarredura]:
    def decorador(funcao: FuncaoVarredura) -> FuncaoVarredura:
        _REGISTRO[nome] = funcao
        return funcao

    return decorador


def remover_varredura(nome: str) -> None:
    _REGISTRO.pop(nome, None)


def nomes_registrados() -> list[str]:
    return sorted(_REGISTRO)


def executar_varreduras(db: Session) -> dict:
    """Executa todas as varreduras registradas e resume o resultado."""
    resultado = {nome: funcao(db) for nome, funcao in sorted(_REGISTRO.items())}
    return {"executado_em": agora().isoformat(), "alteracoes": resultado}

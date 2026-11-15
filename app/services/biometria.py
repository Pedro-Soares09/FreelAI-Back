"""Reconhecimento facial por trás de uma interface trocável.

A validação facial é a etapa que libera o valor integral ao profissional depois da conclusão.
Nesta fase ela é simulada; um fornecedor real entra implementando o mesmo protocolo.
"""

from typing import Protocol

from sqlalchemy.orm import Session


class ReconhecimentoFacial(Protocol):
    def validar(self, db: Session, usuario_id: int, *, selfie_url: str | None = None) -> bool:
        """Confirma que a selfie enviada pertence ao usuário. Devolve `True` quando aprovada."""
        ...


class ReconhecimentoSimulado:
    """Aprova toda validação. Serve ao desenvolvimento e às apresentações."""

    def validar(self, db: Session, usuario_id: int, *, selfie_url: str | None = None) -> bool:
        return True


def obter_reconhecimento() -> ReconhecimentoFacial:
    return ReconhecimentoSimulado()

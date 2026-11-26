from enum import StrEnum


class EstadoConta(StrEnum):
    PENDENTE = "pendente"
    ATIVA = "ativa"
    SUSPENSA = "suspensa"
    BANIDA = "banida"


class PapelUsuario(StrEnum):
    USUARIO = "usuario"
    ADMIN = "admin"


class TipoPerfil(StrEnum):
    PROFISSIONAL = "profissional"
    CONTRATANTE = "contratante"


class TipoPessoa(StrEnum):
    FISICA = "pf"
    JURIDICA = "pj"


class RiscoCategoria(StrEnum):
    BAIXO = "baixo"
    MEDIO = "medio"
    ALTO = "alto"


class StatusAprovacao(StrEnum):
    PENDENTE = "pendente"
    APROVADO = "aprovado"
    RECUSADO = "recusado"


class TipoQualificacao(StrEnum):
    CERTIFICADO = "certificado"
    REGISTRO = "registro"
    DECLARACAO = "declaracao"


class EstadoVaga(StrEnum):
    PUBLICADA = "publicada"
    AGUARDANDO_ACEITE = "aguardando_aceite"
    ACEITA = "aceita"
    AGUARDANDO_PAGAMENTO = "aguardando_pagamento"
    CONTRATADA = "contratada"
    EM_EXECUCAO = "em_execucao"
    ENCERRADA = "encerrada"
    CONCLUIDA = "concluida"
    LIBERADA = "liberada"
    LIBERADA_PARCIAL = "liberada_parcial"
    EXPIRADA = "expirada"
    CANCELADA = "cancelada"
    FALTA = "falta"
    EM_DISPUTA = "em_disputa"
    REEMBOLSADA = "reembolsada"


class TipoVaga(StrEnum):
    EVENTO = "evento"
    AVULSO = "avulso"


class EstadoConvite(StrEnum):
    SUGERIDO = "sugerido"
    INTERESSADO = "interessado"
    ESCOLHIDO = "escolhido"
    ACEITO = "aceito"
    RECUSADO = "recusado"
    EXPIRADO = "expirado"


class EstadoPagamento(StrEnum):
    AGUARDANDO = "aguardando"
    RETIDO = "retido"
    LIBERADO = "liberado"
    LIBERADO_PARCIAL = "liberado_parcial"
    ESTORNADO = "estornado"
    EXPIRADO = "expirado"


class TipoLancamento(StrEnum):
    RETENCAO = "retencao"
    LIBERACAO_PROFISSIONAL = "liberacao_profissional"
    TAXA_PLATAFORMA = "taxa_plataforma"
    ESTORNO = "estorno"


class EstadoDenuncia(StrEnum):
    ABERTA = "aberta"
    PROCEDENTE = "procedente"
    IMPROCEDENTE = "improcedente"


class OrigemDenuncia(StrEnum):
    USUARIO = "usuario"
    AUTOMATICA = "automatica"


class EstadoDisputa(StrEnum):
    ABERTA = "aberta"
    REEMBOLSO_TOTAL = "reembolso_total"
    REEMBOLSO_PARCIAL = "reembolso_parcial"
    PAGAMENTO_INTEGRAL = "pagamento_integral"


class FinalidadeAceite(StrEnum):
    TERMOS = "termos"
    BIOMETRIA = "biometria"
    CONTRATO = "contrato"


class TipoArquivo(StrEnum):
    FOTO = "foto"
    SELFIE = "selfie"
    DOCUMENTO = "documento"
    QUALIFICACAO = "qualificacao"
    EPI = "epi"
    ANEXO = "anexo"
    COMPROVANTE = "comprovante"


class TipoComprovante(StrEnum):
    SERVICO = "servico"
    HORAS_EXTRAS = "horas_extras"
    BONUS = "bonus"

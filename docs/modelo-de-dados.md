# Modelo de dados

Diagrama entidade-relacionamento das tabelas mantidas pelas migrações em `alembic/versions`.
Os modelos ficam em `app/models/` (`nucleo.py` e `contratacao.py`).

```mermaid
erDiagram
    usuarios ||--o| perfis_profissionais : possui
    usuarios ||--o| perfis_contratantes : possui
    usuarios ||--o{ aceites_termos : registra
    usuarios ||--o{ notificacoes : recebe
    usuarios ||--o{ advertencias : recebe
    usuarios ||--o{ mensagens : escreve
    usuarios ||--o{ avaliacoes : avalia
    usuarios ||--o{ denuncias : denuncia

    perfis_profissionais }o--o| enderecos : "localizado em"
    perfis_profissionais }o--o{ categorias : atua
    perfis_profissionais ||--o{ experiencias : declara
    perfis_profissionais ||--o{ qualificacoes : comprova
    perfis_profissionais ||--o{ epis_profissional : envia
    perfis_profissionais ||--o{ convites : recebe

    categorias ||--o{ epis_categoria : exige
    categorias ||--o{ checklist_itens : define
    categorias ||--o{ vagas : classifica

    perfis_contratantes ||--o{ vagas : publica
    vagas }o--|| enderecos : "ocorre em"
    vagas ||--o{ convites : gera
    vagas }o--o| perfis_profissionais : contrata
    vagas ||--o| contratos : formaliza
    vagas ||--o| pagamentos : cobra
    vagas ||--o| conversas : abre
    vagas ||--o| disputas : "pode ter"
    vagas ||--o{ avaliacoes : recebe

    contratos ||--o{ aceites_contrato : "aceito por"
    pagamentos ||--o{ lancamentos : movimenta
    conversas ||--o{ mensagens : contem
```

## Convenções

- Chaves primárias inteiras e nomes de tabela no plural, em português.
- Estados são enums armazenados como texto (compatível com PostgreSQL e SQLite).
- Valores monetários usam `Numeric(10, 2)`.
- Datas e horas são gravadas com fuso e obtidas por `app.tempo.agora()`, que pode ser
  substituído nos testes.
- `lancamentos` é uma tabela de movimentações somente de inserção: o modelo recusa
  atualização e remoção. Correções entram como novos lançamentos.
- Latitude e longitude ficam em colunas `float` na tabela `enderecos`; as distâncias são
  calculadas pela fórmula de Haversine, sem extensões geográficas do banco.

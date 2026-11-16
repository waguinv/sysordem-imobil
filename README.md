# SysOrdem Imobil

SaaS multiempresa para gestão de loteamentos de terrenos e chácaras: empreendimentos, quadras/lotes, vendas, contratos, parcelas, contas a pagar/receber, comissões e site público. Produto da família **SysOrdem** (mesma identidade visual do [SysOrdem Saúde](https://github.com/waguinv/sysordem-saude)). Codinome anterior: *LoteaPro*.

> **Status (16/11/2026): no ar.** Produção em https://imobil.sysordemsistemas.com.br e staging em
> https://staging.imobil.sysordemsistemas.com.br. Fases 0 a 6 concluídas: multiempresa com RLS, RBAC e auditoria;
> empreendimentos, lotes e mapa; CRM, reservas, contratos versionados, parcelas, contas a receber e a pagar,
> comissões, repasses, renegociação, distrato e relatórios; conciliação bancária; site público; portal do comprador;
> planos SaaS; assinatura eletrônica (Documenso); segurança, CI/CD, backups e rollback.
> **Pagamentos reais pelo Asaas**: Pix e boleto na conta de cada loteadora, e a assinatura SaaS na conta da plataforma
> ([docs/PAGAMENTOS_ASAAS.md](docs/PAGAMENTOS_ASAAS.md)).
> **Ainda pendentes**: pentest e revisões jurídica e contábil, console do operador SaaS e correção monetária.
> **Histórico completo e próximos passos: [docs/HISTORICO.md](docs/HISTORICO.md).**

## Stack
- **Web**: Next.js 16 + React 19 + TypeScript + Tailwind CSS 4 (identidade SysOrdem), mobile-first.
- **API**: FastAPI + Pydantic 2 + SQLAlchemy 2 + Alembic, REST em `/api/v1`.
- **Banco**: PostgreSQL 16+ com Row-Level Security por tenant.
- **Testes**: pytest (API, inclusive isolamento entre tenants), Vitest (web); ruff, mypy, ESLint, tsc.

## Executar
- **Com Docker** (recomendado): `cp .env.example .env`, troque os `CHANGE_ME` e rode `docker compose up --build`. Web em http://localhost:3000, API em http://localhost:8000/docs.
- **Sem Docker** (Windows sem WSL, por exemplo): PostgreSQL embarcado em `tools/pg-local` — passo a passo em [docs/DESENVOLVIMENTO.md](docs/DESENVOLVIMENTO.md).

## Estrutura
| Pasta | Conteúdo |
|---|---|
| `apps/api` | API FastAPI, migrations Alembic, testes pytest |
| `apps/web` | Interface Next.js, logos em `public/brand` |
| `docker/` | Script de init do PostgreSQL (papel da aplicação) |
| `tools/pg-local` | PostgreSQL embarcado para dev/testes sem Docker |
| `docs/` | Arquitetura, segurança, desenvolvimento, implantação, testes, identidade visual |
| `PROMPT_MESTRE_CLAUDE_CODE.md` | Instruções e roadmap por fases |

## Regras fundamentais
- Dados isolados por tenant: tenant vem do token assinado, revalidado no banco; RLS no PostgreSQL como 2ª barreira.
- Dinheiro com `Decimal`/`NUMERIC`, nunca float.
- Reservas/vendas transacionais para impedir lote vendido duas vezes.
- Histórico financeiro imutável; correções via estorno. Auditoria append-only.
- Site público sem dados privados de compradores.
- Integrações bancárias por APIs autorizadas, sem armazenar senha bancária.

# SysOrdem Imobil

SaaS multiempresa para gestão de loteamentos de terrenos e chácaras: empreendimentos, quadras/lotes, vendas, contratos, parcelas, contas a pagar/receber, comissões e site público. Produto da família **SysOrdem** (mesma identidade visual do [SysOrdem Saúde](https://github.com/waguinv/sysordem-saude)). Codinome anterior: *LoteaPro*.

> **Status: Fase 3a concluída** — autenticação, multi-tenancy com RLS, RBAC, auditoria; empreendimentos, quadras,
> lotes e mapa; CRM (leads, propostas, escopo do corretor), reservas, contratos versionados e
> **parcelas e contas a receber** (cronograma na assinatura, recebimentos parciais com multa/mora, estornos).
> Ainda **não** há comissões/contas a pagar (3b), renegociação/distrato/relatórios (3c) nem correção monetária.
> Não use dados reais nem publique em produção — ver [docs/IMPLANTACAO.md](docs/IMPLANTACAO.md).

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

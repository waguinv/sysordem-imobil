# Instruções para o Claude Code — SysOrdem Imobil

Leia antes de qualquer tarefa: [docs/HISTORICO.md](docs/HISTORICO.md) (o que foi feito, estado atual e pendências)
e o roadmap original em `PROMPT_MESTRE_CLAUDE_CODE.md`.

## Como trabalhar com o responsável
- **Responda sempre em português do Brasil**, inclusive em notas curtas entre etapas e depois de compactação de contexto.
- Cada entrega tem uma branch e um PR. **Abra o PR ao terminar, sem pedir.** Mescle (merge commit) **só com o "sim" do responsável**.
- Push na `main` faz deploy automático no **staging**. A **produção** é manual (`release.yml`, `workflow_dispatch`)
  e exige aprovação no environment `production`. Só aprove pela API (`pending_deployments`) quando o
  responsável pedir explicitamente.
- Regra de negócio indefinida: adote uma hipótese, documente em `docs/DECISOES_PENDENTES.md` e siga.
- O repositório é **público**: nunca grave segredos, chaves ou dados pessoais em arquivos versionados.

## Desenvolvimento e testes
- API: `apps/api` (FastAPI, SQLAlchemy, Alembic; `.venv/Scripts/python`). Web: `apps/web` (Next.js 16).
- Verificações da API: `ruff check .`, `ruff format --check .`, `mypy` e `pytest`. Os testes de banco exigem
  `TEST_ADMIN_DATABASE_URL` apontando para um superusuário PostgreSQL. Sem Docker local: use o PostgreSQL embarcado de
  `tools/pg-local` (ver `docs/DESENVOLVIMENTO.md`). Docker, compose e scripts de shell só são verificados no CI.
- Verificações da web: `npm run lint`, `npm run typecheck`, `npm test`, `npx prettier --check "src/**/*.{ts,tsx}"`.
- Datas de negócio nos testes: use `app.core.clock.business_today()` (fuso de São Paulo), nunca UTC.
- Migrations sempre "expand/contract" (o deploy faz backup → migrate → troca → health → rollback automático).

## Integrações
- Pagamentos: **Asaas** ([docs/PAGAMENTOS_ASAAS.md](docs/PAGAMENTOS_ASAAS.md)). Para testar no sandbox real, use
  `cd apps/api && python -m scripts.asaas_smoke`. Ele lê `ASAAS_SANDBOX_API_KEY` do `.env` da raiz, que é local e
  ignorado pelo Git, e recusa chave de produção. O Pix exige conta Asaas aprovada.
- Assinatura eletrônica: Documenso auto-hospedado ([docs/ASSINATURA_ELETRONICA.md](docs/ASSINATURA_ELETRONICA.md)).
- Deploy, servidor e backups: [docs/CI_CD.md](docs/CI_CD.md). Os detalhes de acesso ao servidor não ficam no Git. Peça
  ao responsável o arquivo `HISTORICO_OPERACIONAL.md` da pasta de backups dele.

# CI/CD, staging, deploy e rollback (Fase 6a)

## Visão geral
```
PR ──► CI (api, web, infra) ──► merge na main ──► Release: imagens no GHCR (sha-xxxxxxx)
                                                     └─► deploy automático em STAGING
Produção: Actions ▸ "Release e deploy" ▸ Run workflow (environment=production, tag já testada em staging)
Rollback: mesmo workflow com "rollback" marcado (sem tag = volta para a anterior)
```

## CI (`.github/workflows/ci.yml`) — roda em todo PR e na main
| Job | O que verifica |
|---|---|
| `api` | ruff (lint + formato), mypy, pytest com PostgreSQL real (RLS), **migrations ida e volta** (`scripts/check_migrations.py`: upgrade → downgrade base → upgrade, sem tabelas órfãs e com RLS forçado em toda tabela com `tenant_id`), **pip-audit** |
| `web` | ESLint, tsc, Vitest, build de produção, **npm audit** (dependências de produção, nível alto+) |
| `infra` | **gitleaks** (segredos no histórico), **shellcheck** dos scripts, `docker compose config` (dev, perfil assinatura e deploy), build das imagens e **Trivy** (CVEs críticas com correção disponível) |

Proteja a `main` no GitHub (Settings ▸ Branches): exigir PR, os três jobs verdes e branch atualizada.

## Imagens
`release.yml` publica `ghcr.io/<dono>/sysordem-imobil-api:sha-<7>` e `...-web:sha-<7>` a cada push na main. A tag é
o commit, então qualquer versão pode ser reimplantada exatamente igual (sem `latest`).

## Servidor (staging e produção)
Um servidor Linux por ambiente, com Docker Engine + Compose v2.24+.
1. Clone o repositório em, por exemplo, `/opt/sysordem-imobil` (só `docker-compose.yml` e `deploy/` são usados).
2. Crie o `.env` a partir do `.env.example` com segredos próprios do ambiente e:
   - `ENVIRONMENT=staging` ou `production` (produção recusa subir com valores de desenvolvimento — ver SEGURANCA.md);
   - `WEB_DOMAIN`, `API_DOMAIN` (DNS apontando para o servidor; o Caddy emite o HTTPS);
   - `CORS_ORIGINS=["https://<WEB_DOMAIN>"]`;
   - `BACKUP_KEY_FILE=/etc/sysordem/backup.key` (arquivo `chmod 600`, guardado também fora do servidor — sem ele os
     backups não abrem).
3. Se o pacote do GHCR for privado: `docker login ghcr.io` com um token só de leitura de pacotes.
4. Primeira subida: `deploy/deploy.sh sha-xxxxxxx`.

O que fica exposto: portas 80/443 (Caddy). O web inteiro; da API, **só** `/api/v1/webhooks/*` e `/health` — o resto
da API só é acessível pela rede interna (o web chama como BFF). Banco sem porta pública.

### Staging
- Mesmos arquivos e imagens da produção, banco **separado** e **somente dados fictícios** (seed). Nunca copie o
  banco de produção para staging sem anonimizar.
- `ENVIRONMENT=staging` mantém os simuladores (PSP, assinatura, cobrança SaaS) ligados para testes de ponta a ponta.

## Deploy automático (GitHub)
Em Settings ▸ Environments crie `staging` e `production`; em `production` exija aprovação (required reviewers).
Em cada environment:
- variáveis: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_DIR`;
- segredos: `DEPLOY_SSH_KEY` (chave só para deploy) e `DEPLOY_KNOWN_HOSTS` (saída de `ssh-keyscan <host>`
  conferida — o workflow não aceita host desconhecido);
- variável do repositório `DEPLOY_ENABLED=true` liga o job de deploy (sem ela, o release só publica imagens).

## `deploy/deploy.sh <tag>`
1. baixa as imagens da tag;
2. **backup criptografado** do banco (`deploy/backup.sh pre-deploy-<tag>`), se já havia versão no ar;
3. migrations (`migrate`, com o papel dono);
4. troca `api` e `web`, sobe o Caddy;
5. espera `/ready` (API + banco + migrations) e `/healthz` (web) por até 90 s;
6. ok → registra em `.deploy/current_tag`, `previous_tag` e `history.log`;
   falhou → mostra os logs e **volta sozinho** para a versão anterior.

## Rollback
- Automático: quando a versão nova não fica saudável (passo 6).
- Manual: `deploy/deploy.sh --rollback` (volta para `previous_tag`) ou `--rollback sha-xxxxxxx`; pelo GitHub, o
  workflow com "rollback" marcado. **Não roda migrations.**
- **Política de migrations (expand/contract):** toda migration precisa funcionar com a versão anterior do código —
  adicionar colunas/tabelas (nulas ou com padrão) numa versão e só remover/renomear o que ficou sem uso numa versão
  seguinte. Assim o rollback do código nunca exige desfazer schema.
- Se mesmo assim for preciso voltar o schema: `alembic downgrade <revisão>` (testado no CI) **ou** restaurar o backup
  `pre-deploy-<tag>` (`deploy/restore.sh`, que por padrão restaura num banco novo para conferência antes de trocar).

## Backups
`deploy/backup.sh` — `pg_dump -Fc` criptografado (AES-256, chave em arquivo) + sha256, retenção local
`BACKUP_KEEP_DAYS` (14) e cópia externa opcional via rclone (`BACKUP_RCLONE_REMOTE`). Agende diariamente (cron do
servidor), por exemplo `15 3 * * * cd /opt/sysordem-imobil && deploy/backup.sh >> /var/log/imobil-backup.log 2>&1`.
`deploy/restore.sh <arquivo>` restaura num banco novo `imobil_restore_<data>` e confere revisão e empresas.
O teste periódico de restauração entra na Fase 6b.

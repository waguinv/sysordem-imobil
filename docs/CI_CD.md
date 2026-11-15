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
| `drill` | pilha real do Compose: carga + corrida de reservas (`scripts/load_test.py`), `deploy/backup.sh`, `deploy/restore.sh` num banco novo e `scripts/verify_restore.py` (linhas, hashes, RLS) |
| `infra` | **gitleaks** (segredos no histórico), **shellcheck** dos scripts, `docker compose config` (dev, perfil assinatura e deploy), build das imagens e **Trivy** (CVEs críticas com correção disponível) |

Proteja a `main` no GitHub (Settings ▸ Branches): exigir PR, os três jobs verdes e branch atualizada.

## Imagens
`release.yml` publica `ghcr.io/<dono>/sysordem-imobil-api:sha-<7>` e `...-web:sha-<7>` a cada push na main. A tag é
o commit, então qualquer versão pode ser reimplantada exatamente igual (sem `latest`).

## Domínios e o que já está configurado (15/11/2026)
| | Produção | Staging |
|---|---|---|
| `WEB_DOMAIN` | `imobil.sysordemsistemas.com.br` | `staging.imobil.sysordemsistemas.com.br` |
| `API_DOMAIN` (só webhooks) | `api.imobil.sysordemsistemas.com.br` | `api.staging.imobil.sysordemsistemas.com.br` |
| `CORS_ORIGINS` | `["https://imobil.sysordemsistemas.com.br"]` | `["https://staging.imobil.sysordemsistemas.com.br"]` |

DNS hoje: `imobil.sysordemsistemas.com.br` já aponta para `200.141.27.53` — confirme que é o servidor (VPS com
Docker) que vai rodar a produção; se for outra hospedagem, aponte para a VPS. Faltam os registros A (ou CNAME) de
`api.imobil`, `staging.imobil` e `api.staging.imobil`. O Caddy só emite o HTTPS depois que o DNS resolver.

No GitHub (`waguinv/sysordem-imobil`) já estão criados:
- environments `staging` e `production`, ambos só aceitando deploy da `main`; `production` exige aprovação de
  `waguinv`;
- variáveis por environment: `DEPLOY_USER=deploy`, `DEPLOY_DIR=/opt/sysordem-imobil` e `WEB_DOMAIN` (referência);
- variável do repositório `DEPLOY_ENABLED=true`.

Falta, em cada environment: variável `DEPLOY_HOST` (IP ou nome do servidor) e segredos `DEPLOY_SSH_KEY` e
`DEPLOY_KNOWN_HOSTS`. Enquanto faltarem, o push na `main` publica as imagens e o deploy termina com aviso
(sem erro); um disparo manual falha com a lista do que falta.

## Servidor (staging e produção)
Um servidor Linux por ambiente, com Docker Engine + Compose v2.24+.
1. Prepare o acesso de deploy (no servidor, como root):
   ```bash
   adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
   install -d -o deploy -g deploy /opt/sysordem-imobil
   sudo -u deploy git clone https://github.com/waguinv/sysordem-imobil.git /opt/sysordem-imobil
   ```
   No seu computador, gere uma chave só para o deploy e instale a pública no servidor:
   ```bash
   ssh-keygen -t ed25519 -N "" -C "github-deploy-imobil" -f deploy_imobil
   ssh-copy-id -i deploy_imobil.pub deploy@<servidor>
   ssh-keyscan -t ed25519 <servidor>   # confira a impressão digital com o provedor antes de usar
   ```
   Cadastre no environment: `DEPLOY_HOST=<servidor>`, segredo `DEPLOY_SSH_KEY` = conteúdo de `deploy_imobil`
   (privada) e `DEPLOY_KNOWN_HOSTS` = saída do `ssh-keyscan`. Depois apague a chave privada do computador.
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
A restauração é testada a cada PR no job `drill` do CI (backup → restore → conferência linha a linha e do RLS com
`scripts/verify_restore.py`) — ver [REVISAO_SEGURANCA.md](REVISAO_SEGURANCA.md).

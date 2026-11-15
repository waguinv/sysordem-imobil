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

## Ambientes (decidido em 15/11/2026)
Staging e produção no **mesmo servidor** (`200.141.27.53`, Ubuntu), cada um com pasta, banco e portas próprios; o
**nginx do servidor** faz o HTTPS (Let's Encrypt via certbot) e repassa para os containers, que só escutam em
`127.0.0.1`.

| | Produção | Staging |
|---|---|---|
| Sistema (web) | `imobil.sysordemsistemas.com.br` | `staging.imobil.sysordemsistemas.com.br` |
| API pública (só webhooks e /health) | `api.imobil.sysordemsistemas.com.br` | `api.staging.imobil.sysordemsistemas.com.br` |
| Pasta (`DEPLOY_DIR`) | `/opt/sysordem-imobil` | `/opt/sysordem-imobil-staging` |
| `COMPOSE_PROJECT_NAME` | `sysordem-imobil` | `sysordem-imobil-staging` |
| `WEB_HOST_PORT` / `API_HOST_PORT` | `3710` / `8710` | `3720` / `8720` |
| Site do nginx | `deploy/nginx/imobil-production.conf` | `deploy/nginx/imobil-staging.conf` |
| Deploy | manual, com aprovação | automático a cada push na `main` |

DNS: os quatro nomes já apontam para `200.141.27.53`. **A VPS é compartilhada** com os outros sistemas SysOrdem
(saúde, comercial, contábil…, portas 3000–3400 já em uso): por isso as portas 37xx/87xx (35xx já é do Comercial staging) e limites de memória
em todos os containers (`deploy/compose.deploy.yml`) — 1 CPU e ~4 GB de RAM para tudo. Imagens no GHCR são públicas (sem `docker login`).
Um problema no servidor afeta os dois ambientes; o banco de cada um fica num volume próprio.

No GitHub já estão: environments `staging` e `production` (só a `main`; produção exige aprovação de `waguinv`),
variáveis `DEPLOY_USER=deploy` e `DEPLOY_DIR` (acima) e `DEPLOY_ENABLED=true` no repositório.
**Falta em cada environment:** variável `DEPLOY_HOST=200.141.27.53` e segredos `DEPLOY_SSH_KEY` e
`DEPLOY_KNOWN_HOSTS` (passo 1). Enquanto faltarem, o push na `main` publica as imagens e o deploy termina com
aviso; um disparo manual falha listando o que falta.

## Preparar o servidor (uma vez)
1. **Docker e usuário de deploy** (como root):
   ```bash
   # Docker Engine + Compose v2.24+ (https://docs.docker.com/engine/install/ubuntu/)
   adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
   for d in /opt/sysordem-imobil /opt/sysordem-imobil-staging; do
     install -d -o deploy -g deploy "$d"
     sudo -u deploy git clone https://github.com/waguinv/sysordem-imobil.git "$d"
   done
   install -d -m 700 -o deploy -g deploy /etc/sysordem
   ```
   No seu computador, gere uma chave só para o deploy e instale a pública no servidor:
   ```bash
   ssh-keygen -t ed25519 -N "" -C "github-deploy-imobil" -f deploy_imobil
   ssh-copy-id -i deploy_imobil.pub deploy@200.141.27.53
   ssh-keyscan -t ed25519 200.141.27.53   # confira a impressão digital no servidor: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```
   Em **cada** environment do GitHub: variável `DEPLOY_HOST=200.141.27.53`, segredo `DEPLOY_SSH_KEY` = conteúdo de
   `deploy_imobil` (privada) e `DEPLOY_KNOWN_HOSTS` = saída do `ssh-keyscan`. Depois apague a chave privada local.
2. **`.env` de cada pasta** (como `deploy`; `cp .env.example .env`, `chmod 600 .env`) — segredos **diferentes** em
   cada ambiente (gere com `openssl rand -base64 48`):
   - produção: `ENVIRONMENT=production`, `COMPOSE_PROJECT_NAME=sysordem-imobil`, `WEB_HOST_PORT=3710`,
     `API_HOST_PORT=8710`, `CORS_ORIGINS=["https://imobil.sysordemsistemas.com.br"]`;
   - staging: `ENVIRONMENT=staging`, `COMPOSE_PROJECT_NAME=sysordem-imobil-staging`, `WEB_HOST_PORT=3720`,
     `API_HOST_PORT=8720`, `CORS_ORIGINS=["https://staging.imobil.sysordemsistemas.com.br"]`;
   - nos dois: `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `JWT_SECRET` (48+), `PAYMENTS_SANDBOX_SECRET` (só staging;
     em produção deixe vazio), `BACKUP_KEY_FILE=/etc/sysordem/backup-<ambiente>.key` (`chmod 600`; guarde uma cópia
     fora do servidor — sem ela os backups não abrem). Remova as linhas `DOCUMENSO_*`/`SMTP_*` com `CHANGE_ME` ou
     preencha-as.
3. **nginx + HTTPS** (como root, na pasta de produção):
   ```bash
   cp deploy/nginx/imobil-production.conf deploy/nginx/imobil-staging.conf /etc/nginx/sites-available/
   ln -s /etc/nginx/sites-available/imobil-production.conf /etc/nginx/sites-enabled/
   ln -s /etc/nginx/sites-available/imobil-staging.conf /etc/nginx/sites-enabled/
   apt install -y apache2-utils && htpasswd -c /etc/nginx/imobil-staging.htpasswd <usuario>   # senha extra do staging
   nginx -t && systemctl reload nginx
   apt install -y certbot python3-certbot-nginx
   certbot --nginx --redirect -d imobil.sysordemsistemas.com.br -d api.imobil.sysordemsistemas.com.br
   certbot --nginx --redirect -d staging.imobil.sysordemsistemas.com.br -d api.staging.imobil.sysordemsistemas.com.br
   ```
   Recomendado no `nginx.conf`: `server_tokens off;`. Firewall: só 22, 80 e 443 (`ufw allow OpenSSH`,
   `ufw allow 'Nginx Full'`, `ufw enable`).
4. **Primeira subida**: Actions ▸ "Release e deploy" ▸ Run workflow com `environment=staging` (depois
   `production`, que pede sua aprovação). Ou no servidor: `deploy/deploy.sh sha-xxxxxxx` na pasta do ambiente.
5. **Backups diários** (crontab do `deploy`):
   ```
   15 3 * * * cd /opt/sysordem-imobil && deploy/backup.sh >> /opt/sysordem-imobil/backups/backup.log 2>&1
   ```

O que fica exposto: 80/443 (nginx). O web inteiro; da API, **só** `/api/v1/webhooks/*` e `/health` — o resto da API
só é acessível pelo web (BFF). Web, API e banco não têm porta pública.

### Staging
- Somente **dados fictícios** (`docker compose run --rm migrate python -m scripts.seed_dev`). Nunca copie o banco de
  produção para staging sem anonimizar. O site do staging pede uma senha extra do nginx (os usuários de demonstração têm senha pública no repositório) e manda
  `X-Robots-Tag: noindex`; os webhooks do staging não pedem essa senha.
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
4. troca `api` e `web`;
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

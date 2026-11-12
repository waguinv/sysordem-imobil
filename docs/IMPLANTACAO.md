# Implantação

## Local com Docker
1. Instale Docker Desktop/Engine e Compose (no Windows, o Docker Desktop exige WSL 2).
2. Copie `.env.example` para `.env` e troque `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` e `JWT_SECRET` por valores longos e aleatórios.
3. Execute `docker compose up --build`. O serviço `migrate` aplica as migrations com o papel dono; a `api` conecta com o papel da aplicação (sujeito ao RLS), criado por `docker/db/init/01-app-role.sh` na primeira inicialização do volume.
4. (Opcional, dados fictícios) `docker compose run --rm -e MIGRATION_DATABASE_URL=postgresql+psycopg://imobil_owner:<senha>@db:5432/imobil migrate python -m scripts.seed_dev`
5. Acesse `http://localhost:3000`, `http://localhost:8000/health`, `http://localhost:8000/ready` e `http://localhost:8000/docs`.

Sem Docker: ver [DESENVOLVIMENTO.md](DESENVOLVIMENTO.md).

## Parar
`docker compose down`

Atenção: `docker compose down -v` apaga o volume de dados local.

## Tarefas agendadas
- `python -m scripts.expire_reservations` a cada ~5 minutos (mesmas variáveis da API). Ex.: `docker compose run --rm api python -m scripts.expire_reservations` via cron/agendador.

## Checklist de produção
- Segredos em secret manager/variáveis protegidas, nunca as senhas de exemplo.
- HTTPS, domínio e DNS; banco em rede privada.
- Autenticação, RBAC, isolamento multi-tenant e proteção contra abuso implementados e testados.
- Migrations controladas, backup automático criptografado e restauração testada.
- Logs, métricas, alertas, política de retenção e plano de incidentes.
- CORS/cookies, MFA para perfis críticos, storage privado e links temporários.
- Staging separado, CI/CD, rollback e revisão de segurança.
- Revisão jurídica/contábil e processos LGPD com profissionais habilitados.

**Não publicar como produto pronto:** a Fase 0 entrega autenticação, RBAC, isolamento por tenant (com RLS) e auditoria, mas ainda não há módulos de negócio, financeiro, integração bancária, onboarding, recuperação de senha nem as pendências listadas em [SEGURANCA.md](SEGURANCA.md).

# Desenvolvimento local

## Pré-requisitos
- Python 3.12+ e Node.js 20.9+ (testado com Python 3.13 e Node 26).
- PostgreSQL 16+: via Docker Compose **ou** o PostgreSQL embarcado de `tools/pg-local` (não precisa de Docker/WSL).

## 1. Banco sem Docker (PostgreSQL embarcado)
```bash
cd tools/pg-local
npm install
PG_LOCAL_PASSWORD=<senha-local> npm start   # 127.0.0.1:55432, superusuário imobil_owner
```
Em outro terminal, crie o papel da aplicação e o banco (uma vez):
```bash
cd apps/api
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/
.venv/Scripts/python -c "from sqlalchemy import create_engine,text; e=create_engine('postgresql+psycopg://imobil_owner:<senha-local>@127.0.0.1:55432/postgres',isolation_level='AUTOCOMMIT'); c=e.connect(); c.execute(text(\"CREATE ROLE imobil_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '<senha-app>'\")); c.execute(text(\"CREATE DATABASE imobil ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0\"))"
```

> O banco precisa ser **UTF-8** (o `/ready` recusa outra codificação). No Windows, o `initdb` usaria WIN1252 por padrão; o `pg-local` já inicializa em UTF-8.

## 2. Migrations e dados fictícios
```bash
cd apps/api
export MIGRATION_DATABASE_URL=postgresql+psycopg://imobil_owner:<senha-local>@127.0.0.1:55432/imobil
export JWT_SECRET=<segredo-com-32+-caracteres>
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m scripts.seed_dev
```
O seed cria as empresas fictícias `demo` e `outra`. Credenciais **somente locais e fictícias** (definidas em `apps/api/scripts/seed_dev.py`):

| Empresa | E-mail | Perfil |
|---|---|---|
| demo | admin@demo.test | Proprietário |
| demo | leitura@demo.test | Somente leitura |
| outra | admin@outra.test | Proprietário |

Senha de todos: valor de `DEV_PASSWORD` no seed.

## 3. API e Web
```bash
# API (papel da aplicação, sujeito ao RLS)
cd apps/api
DATABASE_URL=postgresql+psycopg://imobil_app:<senha-app>@127.0.0.1:55432/imobil JWT_SECRET=<mesmo-segredo> \
  .venv/Scripts/python -m uvicorn app.main:app --port 8000

# Web
cd apps/web && npm install
API_INTERNAL_URL=http://127.0.0.1:8000 npm run dev
```
No Claude Code (desktop), `.claude/launch.json` já define as configurações `api` e `web` para o preview.

## Testes e verificações
```bash
cd apps/api
TEST_ADMIN_DATABASE_URL=postgresql+psycopg://imobil_owner:<senha-local>@127.0.0.1:55432/postgres \
  .venv/Scripts/python -m pytest          # 90 testes; sem a variável, os testes de banco são pulados
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/mypy

cd apps/web
npm run lint && npm run typecheck && npm test && npm run build
```
Os testes de banco criam o banco `imobil_test` e o papel `imobil_app_test` (sem superuser/BYPASSRLS), aplicam as migrations e conectam a API como esse papel — o RLS é exercitado de verdade.

## Logo
As logos em `apps/web/public/brand` são geradas por `apps/web/scripts/gerar_logo.py` a partir da logo oficial SysOrdem (símbolo + SYSORDEM) com a segunda linha "IMOBIL" em Inter SemiBold. Ver [IDENTIDADE_VISUAL.md](IDENTIDADE_VISUAL.md).

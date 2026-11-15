#!/usr/bin/env bash
# Restaura um backup criado por deploy/backup.sh.
#   deploy/restore.sh <arquivo.dump.enc> [banco-destino]
# Sem banco-destino, restaura num banco NOVO "imobil_restore_<data>" (teste de restauração) e não toca
# no banco em uso. Para substituir o banco em uso, pare api/web e passe o nome dele explicitamente —
# o script pede confirmação digitada.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

FILE="${1:?uso: deploy/restore.sh <arquivo.dump.enc> [banco-destino]}"
KEY="${BACKUP_KEY_FILE:?defina BACKUP_KEY_FILE}"
LIVE_DB="${POSTGRES_DB:-imobil}"
TARGET="${2:-imobil_restore_$(date -u +%Y%m%d%H%M%S)}"
DB_USER="${POSTGRES_USER:-imobil_owner}"
COMPOSE=(docker compose -f docker-compose.yml)
if [ -n "${IMAGE_TAG:-}" ]; then COMPOSE+=(-f deploy/compose.deploy.yml); fi

[[ "$TARGET" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] || { echo "nome de banco inválido: $TARGET" >&2; exit 1; }
if [ -f "$FILE.sha256" ]; then
  (cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256")
else
  echo "aviso: sem $FILE.sha256 — integridade não conferida" >&2
fi

psql_admin() { "${COMPOSE[@]}" exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d postgres "$@"; }

if [ "$TARGET" = "$LIVE_DB" ]; then
  read -r -p "Isto SUBSTITUI o banco '$LIVE_DB'. Digite o nome do banco para confirmar: " ok
  [ "$ok" = "$LIVE_DB" ] || { echo "cancelado"; exit 1; }
  psql_admin -c "DROP DATABASE IF EXISTS \"$LIVE_DB\" WITH (FORCE)"
fi
psql_admin -c "CREATE DATABASE \"$TARGET\" ENCODING 'UTF8' TEMPLATE template0"

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$KEY" -in "$FILE" \
  | "${COMPOSE[@]}" exec -T db pg_restore -U "$DB_USER" -d "$TARGET" --exit-on-error --single-transaction

# Conferência mínima: revisão das migrations e contagem de empresas.
"${COMPOSE[@]}" exec -T db psql -U "$DB_USER" -d "$TARGET" -At \
  -c "SELECT 'revisao=' || version_num FROM alembic_version" \
  -c "SELECT 'empresas=' || count(*) FROM tenants"
echo "restaurado em: $TARGET"

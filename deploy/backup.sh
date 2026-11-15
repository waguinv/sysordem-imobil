#!/usr/bin/env bash
# Backup lógico do banco (pg_dump -Fc) criptografado com AES-256 (openssl, senha em arquivo).
#   deploy/backup.sh [rótulo]
# Variáveis (no .env ou ambiente):
#   BACKUP_DIR            pasta de destino (padrão ./backups)
#   BACKUP_KEY_FILE       arquivo com a senha de criptografia (obrigatório; fora do Git e fora de BACKUP_DIR)
#   BACKUP_KEEP_DAYS      retenção local em dias (padrão 14)
#   BACKUP_RCLONE_REMOTE  opcional: destino rclone para cópia fora do servidor (ex.: s3:bucket/imobil)
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

LABEL="${1:-diario}"
DIR="${BACKUP_DIR:-./backups}"
KEY="${BACKUP_KEY_FILE:?defina BACKUP_KEY_FILE (arquivo com a senha dos backups)}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
DB="${POSTGRES_DB:-imobil}"
DB_USER="${POSTGRES_USER:-imobil_owner}"
COMPOSE=(docker compose -f docker-compose.yml)
if [ -n "${IMAGE_TAG:-}" ]; then COMPOSE+=(-f deploy/compose.deploy.yml); fi

[[ "$LABEL" =~ ^[A-Za-z0-9._-]{1,80}$ ]] || { echo "rótulo inválido: $LABEL" >&2; exit 1; }
[ -r "$KEY" ] || { echo "BACKUP_KEY_FILE ilegível: $KEY" >&2; exit 1; }
mkdir -p "$DIR"
umask 077
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DIR/imobil-$STAMP-$LABEL.dump.enc"

# O dump sai do container já comprimido (-Fc) e é criptografado antes de tocar o disco.
"${COMPOSE[@]}" exec -T db pg_dump -U "$DB_USER" -d "$DB" -Fc \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY" -out "$OUT.partial"
mv "$OUT.partial" "$OUT"
(cd "$DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
echo "backup: $OUT ($(du -h "$OUT" | cut -f1))"

if [ -n "${BACKUP_RCLONE_REMOTE:-}" ]; then
  rclone copy "$OUT" "$BACKUP_RCLONE_REMOTE/"
  rclone copy "$OUT.sha256" "$BACKUP_RCLONE_REMOTE/"
  echo "cópia externa: $BACKUP_RCLONE_REMOTE"
fi

find "$DIR" -maxdepth 1 -name 'imobil-*.dump.enc*' -mtime +"$KEEP" -print -delete

#!/usr/bin/env bash
# Backup lógico do banco (pg_dump -Fc) criptografado com AES-256 (openssl, senha em arquivo).
#   deploy/backup.sh [rótulo]
# Variáveis (no .env ou ambiente):
#   BACKUP_DIR            pasta de destino (padrão ./backups)
#   BACKUP_KEY_FILE       arquivo com a senha de criptografia (obrigatório; fora do Git e fora de BACKUP_DIR)
#   BACKUP_KEEP_DAYS      retenção local em dias (padrão 14)
#   BACKUP_RCLONE_REMOTE  opcional: destino rclone para a cópia fora do servidor (deploy/offsite_backup.sh)
#   BACKUP_HEALTHCHECK_URL opcional: URL de monitor (ex.: healthchecks.io). O backup diário avisa
#                         "<url>" no sucesso e "<url>/fail" na falha — e o monitor alerta se o aviso não vier.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

LABEL="${1:-diario}"

# Aviso ao monitor só no backup agendado (os de antes do deploy aparecem no próprio deploy).
ping_monitor() {
  [ -n "${BACKUP_HEALTHCHECK_URL:-}" ] && [ "$LABEL" = "${BACKUP_HEALTHCHECK_LABEL:-diario}" ] || return 0
  curl -fsS -m 10 --retry 3 -o /dev/null "${BACKUP_HEALTHCHECK_URL%/}$1" \
    || echo "aviso: monitor de backup não respondeu (${1:-ok})" >&2
}
# Qualquer saída com erro (comando que falhou ou `exit 1` de uma checagem) avisa a falha.
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then ping_monitor /fail; fi
}
trap on_exit EXIT
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

# Cópia fora do servidor (envia também o que ficou para trás e confere tudo). Se falhar, o script para
# AQUI — antes da limpeza local: nenhum backup é apagado do servidor sem estar conferido lá fora.
if [ -n "${BACKUP_RCLONE_REMOTE:-}" ]; then
  deploy/offsite_backup.sh
else
  echo "aviso: sem BACKUP_RCLONE_REMOTE — o backup ficou só neste servidor" >&2
fi

find "$DIR" -maxdepth 1 -name 'imobil-*.dump.enc*' -mtime +"$KEEP" -print -delete
ping_monitor ""

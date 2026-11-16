#!/usr/bin/env bash
# Cópia dos backups para FORA do servidor (rclone), com conferência.
#   deploy/offsite_backup.sh
# Chamado pelo deploy/backup.sh depois de cada dump; pode rodar sozinho para pôr em dia.
#
# - Envia todos os backups locais que ainda não estão no destino (um dia que falhou é recuperado no
#   próximo) e confere TODOS com `rclone check` (tamanho e hash). Sai com erro se faltar ou divergir.
# - `--immutable`: nunca sobrescreve um backup que já está no destino (se houver um arquivo diferente
#   com o mesmo nome, falha em vez de trocar).
# - Nunca apaga nada no destino. A retenção fica numa regra do próprio bucket (lifecycle / bloqueio de
#   retenção) e a credencial do servidor deve poder só gravar e listar: um servidor invadido não
#   consegue apagar as cópias. Ver docs/CI_CD.md → Backups.
# - Os arquivos já saem cifrados (AES-256) do backup.sh; a chave NÃO vai para o mesmo destino.
#
# Variáveis (no .env ou ambiente):
#   BACKUP_DIR            pasta dos backups locais (padrão ./backups)
#   BACKUP_RCLONE_REMOTE  destino rclone (ex.: r2:sysordem-backups/producao) — obrigatório aqui
#   RCLONE_CONFIG         opcional: arquivo de configuração do rclone (padrão do rclone)
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

DIR="${BACKUP_DIR:-./backups}"
REMOTE="${BACKUP_RCLONE_REMOTE:?defina BACKUP_RCLONE_REMOTE (destino rclone fora do servidor)}"
REMOTE="${REMOTE%/}"
command -v rclone > /dev/null || { echo "rclone não instalado (veja docs/CI_CD.md → Backups)" >&2; exit 1; }
[ -d "$DIR" ] || { echo "pasta de backups não existe: $DIR" >&2; exit 1; }

FILTER=(--include 'imobil-*.dump.enc' --include 'imobil-*.dump.enc.sha256')
shopt -s nullglob
local_files=("$DIR"/imobil-*.dump.enc)
shopt -u nullglob
if [ "${#local_files[@]}" -eq 0 ]; then
  echo "cópia externa: nenhum backup local para enviar"
  exit 0
fi

# Só arquivos completos: o backup.sh grava em .partial e renomeia no fim.
rclone copy "$DIR" "$REMOTE" "${FILTER[@]}" --immutable --no-update-modtime --retries 5 \
  --low-level-retries 10 --stats-log-level NOTICE --stats 0
rclone check "$DIR" "$REMOTE" "${FILTER[@]}" --one-way

umask 077
printf '%s %s arquivo(s) conferido(s) em %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${#local_files[@]}" "$REMOTE" \
  > "$DIR/offsite.ok"
echo "cópia externa: ${#local_files[@]} backup(s) conferido(s) em $REMOTE"

#!/usr/bin/env bash
# Monitor do servidor: confere a saúde do ambiente e avisa um serviço de alertas (healthchecks.io).
#   deploy/monitor.sh            (cron do usuário deploy, a cada 5 min — ver docs/CI_CD.md → Monitoramento)
#
# Confere:
#   - API: /ready e /ops/status (5xx demais, banco lento ou fora, migrations diferentes do código);
#   - site: a página de login responde;
#   - disco: uso da partição abaixo do limite;
#   - backups: o último backup local é recente e, com cópia externa ligada, a última conferência também.
# Resultado: MONITOR_HEALTHCHECK_URL recebe "<url>" (tudo bem) ou "<url>/fail" com o relatório no corpo.
# O healthchecks.io alerta na falha E quando o aviso para de chegar (servidor fora do ar, cron parado).
# Sem a URL, só imprime o relatório e sai com 1 se houver problema.
#
# Variáveis (no .env ou ambiente):
#   MONITOR_HEALTHCHECK_URL   URL do check (healthchecks.io), período 5 min / tolerância ~10 min
#   OPS_TOKEN                 token da rota interna /ops/status (o mesmo do container da API)
#   API_HOST_PORT / API_PORT  porta local da API (deploy: API_HOST_PORT)
#   WEB_HOST_PORT             porta local do site (sem ela, o site não é conferido)
#   MONITOR_DISK_MAX_PCT      uso máximo do disco em % (padrão 85)
#   MONITOR_BACKUP_MAX_HOURS  idade máxima do último backup (padrão 26)
#   BACKUP_DIR, BACKUP_RCLONE_REMOTE (os mesmos do backup.sh)
set -Euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  # sem -u ao carregar: um valor com "$" sem aspas no .env não pode derrubar o monitor
  set +u -a
  # shellcheck disable=SC1091
  . ./.env
  set -u +a
fi

API="http://127.0.0.1:${API_HOST_PORT:-${API_PORT:-8000}}"
WEB_PORT="${WEB_HOST_PORT:-}"
DISK_MAX="${MONITOR_DISK_MAX_PCT:-85}"
BACKUP_MAX_H="${MONITOR_BACKUP_MAX_HOURS:-26}"
DIR="${BACKUP_DIR:-./backups}"
problems=()
notes=()

# 1) API pronta (banco acessível e migrations aplicadas)
if ! curl -fsS -m 10 -o /dev/null "$API/ready"; then
  problems+=("API não está pronta ($API/ready)")
fi

# 2) Saúde interna da API (5xx, banco lento, migrations)
if [ -n "${OPS_TOKEN:-}" ]; then
  body="$(curl -sS -m 10 -H "X-Ops-Token: $OPS_TOKEN" -w '\n%{http_code}' "$API/ops/status" || true)"
  code="${body##*$'\n'}"
  json="${body%$'\n'*}"
  case "$code" in
    200) notes+=("api: $json") ;;
    503) problems+=("API degradada: $json") ;;
    404) problems+=("OPS_TOKEN não confere com o da API (ou a API não tem OPS_TOKEN)") ;;
    *) problems+=("/ops/status respondeu ${code:-sem resposta}") ;;
  esac
else
  notes+=("aviso: sem OPS_TOKEN — saúde interna da API não conferida")
fi

# 3) Site
if [ -n "$WEB_PORT" ]; then
  if ! curl -fsS -m 15 -o /dev/null "http://127.0.0.1:$WEB_PORT/entrar"; then
    problems+=("site não responde (porta $WEB_PORT)")
  fi
fi

# 4) Disco
used="$(df -P . | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [ -n "$used" ] && [ "$used" -ge "$DISK_MAX" ]; then
  problems+=("disco em ${used}% (limite ${DISK_MAX}%)")
else
  notes+=("disco: ${used:-?}%")
fi

# 5) Backups: o último local e, com cópia externa, a última conferência lá fora
age_hours() { echo $(( ($(date +%s) - $(stat -c %Y "$1")) / 3600 )); }
latest="$(find "$DIR" -maxdepth 1 -name 'imobil-*.dump.enc' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
if [ -z "$latest" ]; then
  problems+=("nenhum backup em $DIR")
else
  h="$(age_hours "$latest")"
  if [ "$h" -ge "$BACKUP_MAX_H" ]; then
    problems+=("último backup tem ${h} h ($(basename "$latest"))")
  else
    notes+=("backup: ${h} h")
  fi
fi
if [ -n "${BACKUP_RCLONE_REMOTE:-}" ]; then
  if [ ! -f "$DIR/offsite.ok" ]; then
    problems+=("cópia externa nunca conferida (sem $DIR/offsite.ok)")
  else
    h="$(age_hours "$DIR/offsite.ok")"
    if [ "$h" -ge "$BACKUP_MAX_H" ]; then
      problems+=("última cópia externa conferida há ${h} h")
    fi
  fi
fi

# Relatório e aviso
host="$(hostname 2>/dev/null || echo servidor)"
if [ "${#problems[@]}" -eq 0 ]; then
  report="OK ${host} $(date -u +%Y-%m-%dT%H:%M:%SZ)"$'\n'"$(printf '%s\n' "${notes[@]}")"
  suffix=""
  rc=0
else
  report="PROBLEMA ${host} $(date -u +%Y-%m-%dT%H:%M:%SZ)"$'\n'"$(printf -- '- %s\n' "${problems[@]}")"$'\n'"$(printf '%s\n' "${notes[@]}")"
  suffix="/fail"
  rc=1
fi
echo "$report"
if [ -n "${MONITOR_HEALTHCHECK_URL:-}" ]; then
  # o corpo (até 10 kB) aparece no painel e no alerta do healthchecks.io
  curl -fsS -m 10 --retry 3 -o /dev/null --data-raw "${report:0:10000}" "${MONITOR_HEALTHCHECK_URL%/}$suffix" \
    || echo "aviso: o serviço de alertas não respondeu" >&2
fi
exit "$rc"

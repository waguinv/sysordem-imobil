#!/usr/bin/env bash
# Implanta uma versão (tag de imagem do GHCR) no servidor de staging/produção: backup antes,
# migrations, verificação de saúde e volta automática para a versão anterior se a nova não subir.
#   deploy/deploy.sh <tag>              implanta (backup + migrate + troca)
#   deploy/deploy.sh --rollback [tag]   volta para a tag anterior (ou a informada) SEM rodar migrations
# Pré-requisitos no servidor: este repositório, .env, Docker Compose v2.24+ e login no GHCR se o pacote
# for privado. Ver docs/CI_CD.md.
set -Eeuo pipefail
cd "$(dirname "$0")/.."

STATE=.deploy
mkdir -p "$STATE"
ROLLBACK=0
if [ "${1:-}" = "--rollback" ]; then
  ROLLBACK=1
  TAG="${2:-$(cat "$STATE/previous_tag" 2>/dev/null || true)}"
else
  TAG="${1:?uso: deploy/deploy.sh <tag> | --rollback [tag]}"
fi
[ -n "$TAG" ] || { echo "sem tag anterior registrada" >&2; exit 1; }
[[ "$TAG" =~ ^[A-Za-z0-9._-]{1,128}$ ]] || { echo "tag inválida: $TAG" >&2; exit 1; }

CURRENT="$(cat "$STATE/current_tag" 2>/dev/null || true)"
export IMAGE_TAG="$TAG"
COMPOSE=(docker compose -f docker-compose.yml -f deploy/compose.deploy.yml)
MODE=""
if [ "$ROLLBACK" = 1 ]; then MODE=" (rollback)"; fi

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

api_ready() {
  "${COMPOSE[@]}" exec -T api python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"
}
web_ready() {
  "${COMPOSE[@]}" exec -T web node -e \
    "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
}
healthy() {
  local _
  for _ in $(seq 1 45); do
    if api_ready >/dev/null 2>&1 && web_ready >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

log "versão atual: ${CURRENT:-nenhuma} -> nova: $TAG$MODE"
"${COMPOSE[@]}" pull migrate api web
"${COMPOSE[@]}" up -d db

if [ "$ROLLBACK" = 0 ]; then
  if [ -n "$CURRENT" ]; then
    log "backup antes das migrations"
    deploy/backup.sh "pre-deploy-$TAG"
  fi
  log "migrations"
  "${COMPOSE[@]}" run --rm migrate
fi

log "subindo api e web"
"${COMPOSE[@]}" up -d --no-deps api web
"${COMPOSE[@]}" up -d caddy

if healthy; then
  if [ "$CURRENT" != "$TAG" ]; then
    if [ -n "$CURRENT" ]; then echo "$CURRENT" > "$STATE/previous_tag"; fi
    echo "$TAG" > "$STATE/current_tag"
  fi
  echo "$(date -u +%FT%TZ) $TAG ok$MODE" >> "$STATE/history.log"
  log "ok: $TAG no ar"
  docker image prune -f >/dev/null || true
  exit 0
fi

log "FALHOU: $TAG não ficou saudável"
"${COMPOSE[@]}" logs --tail=80 api web || true
echo "$(date -u +%FT%TZ) $TAG falhou$MODE" >> "$STATE/history.log"
if [ -n "$CURRENT" ] && [ "$CURRENT" != "$TAG" ]; then
  log "voltando para $CURRENT (migrations já aplicadas NÃO são desfeitas — política expand/contract)"
  export IMAGE_TAG="$CURRENT"
  "${COMPOSE[@]}" up -d --no-deps api web
  if healthy; then log "rollback ok: $CURRENT"; else log "rollback também falhou — ver docs/CI_CD.md"; fi
fi
exit 1

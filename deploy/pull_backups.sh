#!/usr/bin/env bash
# Cópia dos backups do servidor para ESTE computador (cópia fora da VPS), sob demanda.
#   deploy/pull_backups.sh [destino] [host-ssh]
# Padrões: destino ../imobil-backups (fora do repositório) e host "sysordem-vps" (~/.ssh/config).
# Copia só arquivos novos (os backups não mudam depois de gravados), confere o sha256 de cada um
# e traz as chaves de backup na primeira vez — sem elas nenhum backup abre. Guarde o destino em
# lugar seguro (as chaves abrem os dados de todos os clientes).
set -euo pipefail

dest="${1:-$(cd "$(dirname "$0")/../.." && pwd)/imobil-backups}"
host="${2:-sysordem-vps}"
ssh_opts=(-o BatchMode=yes)

mkdir -p "$dest/chaves"
chmod 700 "$dest" "$dest/chaves" 2>/dev/null || true

for env in producao staging; do
  if [ "$env" = producao ]; then dir=/opt/sysordem-imobil; key=backup-production.key
  else dir=/opt/sysordem-imobil-staging; key=backup-staging.key; fi

  mkdir -p "$dest/$env"
  if [ ! -s "$dest/chaves/$key" ]; then
    scp -q "${ssh_opts[@]}" "$host:/etc/sysordem/$key" "$dest/chaves/$key"
    chmod 600 "$dest/chaves/$key" 2>/dev/null || true
  fi

  novos=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    nome="$(basename "$f")"
    [ -s "$dest/$env/$nome" ] && continue
    scp -q "${ssh_opts[@]}" "$host:$f" "$host:$f.sha256" "$dest/$env/"
    (cd "$dest/$env" && sha256sum -c --quiet "$nome.sha256")
    novos=$((novos + 1))
  done < <(ssh "${ssh_opts[@]}" "$host" "ls -1 $dir/backups/*.dump.enc 2>/dev/null || true")

  echo "$env: $novos backup(s) novo(s) em $dest/$env"
done

#!/bin/bash
set -euo pipefail

# ─── Self-exec ─────────────────────────────────────────────────────────────
# Ensure this script is executable on first run
[ -x "$0" ] || chmod +x "$0"

# ─── Config ────────────────────────────────────────────────────────────────
NETWORKS=("agency_analytics_net" "agency_internal_net")

# ─── Functions ─────────────────────────────────────────────────────────────
log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

network_exists() {
  docker network inspect "$1" > /dev/null 2>&1
}

create_network() {
  local name="$1"
  if network_exists "$name"; then
    log "OK: Network '$name' already exists. Skipping."
  else
    log "Creando network '$name'..."
    if docker network create "$name"; then
      log "OK: Network '$name' creada exitosamente."
    else
      log "ERROR: No se pudo crear la network '$name'."
      return 1
    fi
  fi
}

# ─── Main ──────────────────────────────────────────────────────────────────
log "=== Inicializando redes Docker ==="

for net in "${NETWORKS[@]}"; do
  create_network "$net"
done

log "=== Redes Docker verificadas ==="
docker network ls --filter "name=agency_" --format "table {{.Name}}\t{{.Driver}}\t{{.Scope}}"

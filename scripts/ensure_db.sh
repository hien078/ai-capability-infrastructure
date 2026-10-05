#!/usr/bin/env bash
# Keep the ACI Postgres container up with ALL its published ports (Arch host).
#
# docker-compose.override.yml publishes 5432 on the Tailscale IP so the Mac
# ACI server can reach aci_bench. At boot docker starts the container before
# tailscale0 has that IP -> "cannot assign requested address" -> the container
# stays Exited and docker never retries; `docker start` alone does not
# republish ports. This script waits for the Tailscale IP + docker, then
# force-recreates the db service when the Tailscale port is missing (data
# lives in the named volume aci_pgdata, so recreate is lossless).
#
# Idempotent: a healthy container with both ports is left untouched.
# Run by deploy/systemd/aci-db-ensure.{service,timer} (systemd --user).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${ACI_DB_CONTAINER:-aci-db-1}"
WAIT_SECONDS="${ACI_DB_WAIT_SECONDS:-300}"
TS_IP="$(grep -oE '[0-9]+(\.[0-9]+){3}:5432:5432' "$REPO/docker-compose.override.yml" | cut -d: -f1 | head -1)"

log() { echo "aci-db-ensure: $*"; }

deadline=$((SECONDS + WAIT_SECONDS))
until docker info >/dev/null 2>&1; do
    ((SECONDS < deadline)) || { log "docker not ready after ${WAIT_SECONDS}s"; exit 1; }
    sleep 5
done
if [[ -n "$TS_IP" ]]; then
    until ip -4 addr show 2>/dev/null | grep -q "inet ${TS_IP}/"; do
        ((SECONDS < deadline)) || { log "tailscale IP ${TS_IP} absent after ${WAIT_SECONDS}s"; exit 1; }
        sleep 5
    done
fi

ports="$(docker port "$CONTAINER" 5432 2>/dev/null || true)"
running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo missing)"
if [[ "$running" == "true" && "$ports" == *"127.0.0.1:5432"* && ( -z "$TS_IP" || "$ports" == *"${TS_IP}:5432"* ) ]]; then
    log "ok (${CONTAINER} running, ports published)"
    exit 0
fi

log "repairing: running=${running} ports=[${ports//$'\n'/ }]"
cd "$REPO"
docker compose up -d --force-recreate db
log "recreated; published: $(docker port "$CONTAINER" 5432 | tr '\n' ' ')"

#!/usr/bin/env bash
# Canonical redeploy command for a single service, once a droplet has been
# migrated to Podman + quadlets (see docs/podman-migration-plan.md).
#
# Usage (run from ~/india-news-backend on the droplet):
#   ./deploy/deploy.sh app
#   ./deploy/deploy.sh contentworker
#   ./deploy/deploy.sh pollworker
#   ./deploy/deploy.sh narrator      # newsapp only — see narrator.container
#
# Exists specifically to prevent the 2026-09-16 incident: `docker cp`/`podman
# cp` was used as a deploy shortcut for timeline_scheduler, silently leaving
# the running process on stale in-memory code for ~2 days because `cp` never
# restarts anything. This script always rebuilds the image AND restarts the
# unit, every time, so "I deployed" and "the new code is actually running"
# can never drift apart again. Never use `podman cp` to ship a code change —
# it's fine for one-off inspection/debugging only.

set -euo pipefail

SERVICE="${1:-}"
VALID_SERVICES=(app contentworker pollworker narrator)

if [[ -z "$SERVICE" ]]; then
    echo "usage: $0 <service>" >&2
    echo "  valid services: ${VALID_SERVICES[*]}" >&2
    exit 1
fi

if [[ ! " ${VALID_SERVICES[*]} " =~ " ${SERVICE} " ]]; then
    echo "error: unknown service '$SERVICE'" >&2
    echo "  valid services: ${VALID_SERVICES[*]}" >&2
    exit 1
fi

echo "=== deploying $SERVICE ==="

echo "--- git pull ---"
git pull

echo "--- podman build ---"
podman build -t "localhost/india-news-backend-app:latest" .

echo "--- systemctl restart ${SERVICE}.service ---"
# NOTE: the quadlet unit FILE is <service>.container, but the systemd unit it
# generates is <service>.service — systemctl operates on the .service name,
# never .container (confirmed against a real `systemctl list-unit-files`
# during the newsapp-2 pilot, 2026-09-16 — an earlier draft of this script
# got this wrong and failed with "Unit not found").
systemctl restart "${SERVICE}.service"

echo "--- confirming restart ---"
sleep 2
STARTED_AT=$(systemctl show "${SERVICE}.service" --property=ActiveEnterTimestamp --value)
echo "StartedAt: ${STARTED_AT}"
systemctl is-active --quiet "${SERVICE}.service" && echo "status: active" || {
    echo "WARNING: ${SERVICE}.service is not active after restart — check: journalctl -u ${SERVICE}.service -n 50" >&2
    exit 1
}

echo "=== $SERVICE deployed ==="

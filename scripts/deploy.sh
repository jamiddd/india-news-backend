#!/usr/bin/env bash
# Pull latest main and rebuild/restart the prod backend container on this
# droplet. Run directly on newsapp or newsapp-2 (not from your machine) —
# each droplet deploys independently, so run this on BOTH after a backend
# change, one at a time so they're never both down at once.
set -euo pipefail

cd /root/india-news-backend

git pull origin main
docker compose -f docker-compose.prod.yml up -d --build

echo
echo "Deployed. Tailing logs (Ctrl-C to stop watching, container keeps running):"
docker compose -f docker-compose.prod.yml logs -f --tail=50 app

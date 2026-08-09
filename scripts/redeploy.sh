#!/bin/bash
# Pull latest code, rebuild the backend image from scratch, and bring the
# production stack back up. Run this from ~/india-news-backend on the droplet
# whenever a backend code change needs to go live.
set -e

cd "$(dirname "$0")/.."

echo "================================================================="
echo "  Pulling latest code..."
echo "================================================================="
git pull origin main
git log --oneline -1

echo "================================================================="
echo "  Rebuilding image (no cache) and restarting stack..."
echo "================================================================="
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d

echo "================================================================="
echo "  Waiting for containers to report healthy..."
echo "================================================================="
sleep 5
docker compose -f docker-compose.prod.yml ps

echo "================================================================="
echo "  Applying schema migrations (idempotent, safe to re-run)..."
echo "================================================================="
docker exec news_backend_prod python3 scripts/add_content_column.py
docker exec news_backend_prod python3 scripts/migrate_users_provider_uid_index.py
docker exec news_backend_prod python3 scripts/add_ranking_columns.py
docker exec news_backend_prod python3 scripts/add_ai_enriched_column.py

echo "================================================================="
echo "  Running enrichment pass (picks up any never-enriched clusters)..."
echo "================================================================="
docker exec news_backend_prod python3 scripts/enrich_all_clusters.py

echo "================================================================="
echo "  Last 20 log lines:"
echo "================================================================="
docker logs news_backend_prod --tail 20

echo "================================================================="
echo "  Done. Verify with: curl http://localhost:8080/api/v1/clusters?limit=3"
echo "================================================================="

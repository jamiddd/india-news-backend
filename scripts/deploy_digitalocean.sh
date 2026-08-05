#!/bin/bash
# DigitalOcean Droplet Setup & Single-Command Deployment Script
set -e

echo "================================================================="
echo "   India News App — DigitalOcean Production Deployment Script    "
echo "================================================================="

# 1. Update system & install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# 2. Build and start production containers
echo "Starting Production Docker containers..."
docker compose -f docker-compose.prod.yml up -d --build

# 3. Wait for database readiness
echo "Waiting for database initialization..."
sleep 5

# 4. Seed initial sources inside container
echo "Seeding 16 verified RSS feeds..."
docker exec news_backend_prod python3 scripts/seed_sources.py

# 5. Run initial ingestion sweep
echo "Executing initial RSS ingestion sweep..."
docker exec news_backend_prod python3 scripts/run_poller_now.py

echo "================================================================="
echo " ✅ DEPLOYMENT COMPLETE! Backend is live on http://$(curl -s ifconfig.me):8080"
echo "================================================================="

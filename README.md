# 🇮🇳 India News Clustered Intelligence — FastAPI Backend

Multi-Outlet News Aggregation & AI Framing Analysis Engine for Indian Media Publishers.

## 🌟 Key Features
- **16 Verified Production RSS Feeds**: National, Business, Official PIB, and Northeast India regional publishers (*The Hindu*, *Indian Express*, *NDTV*, *Livemint*, *Assam Tribune*, *EastMojo*, *PIB*, etc.).
- **2-Pass Deduplication & Clustering**:
  - Pass 1: Canonical URL SHA-256 exact matching.
  - Pass 2: 64-bit SimHash bitwise fingerprinting & Hamming distance calculation.
- **AI & Entity Enrichment**: Anthropic Claude AI + Rule-based fallback entity extraction (*RBI*, *SEBI*, *Supreme Court*, *Ministry of Finance*) & framing angle comparison.
- **FastAPI REST API**:
  - `GET /api/v1/clusters`: Paginated feed with category filtering (`all`, `national`, `business`, `official`, `northeast`).
  - `GET /api/v1/clusters/{id}`: Cluster detail view.
  - `GET /api/v1/search?q=...`: Full-text search across headlines and summaries.
  - `POST /api/v1/ingest/poll`: Trigger background ingestion poller.
  - `POST /api/v1/clusters/{id}/enrich`: Trigger AI enrichment.
- **Moderated community news**: allowlisted users can create drafts and submissions; admins approve, reject, audit, report, withdraw, or take down posts in a separate feed.

## 🚀 Quickstart (Local Development)

### 1. Requirements
- Python 3.9+
- PostgreSQL 16 & Redis 7 (or via Docker)

### 2. Setup Database & Start Stack
```bash
docker compose up -d postgres redis
pip install -r requirements.txt
python3 scripts/seed_sources.py
python3 scripts/run_poller_now.py
uvicorn app.main:app --reload --port 8000
```

## ☁️ DigitalOcean Production Deployment

Before deploying community news, set `COMMUNITY_ALLOWED_EMAILS` and `COMMUNITY_ADMIN_EMAILS` in the deployment environment. These values must match the allowlist in `CommunityScreen.kt`; the backend remains the source of truth and denies access when either list is empty.

```bash
chmod +x scripts/deploy_digitalocean.sh
./scripts/deploy_digitalocean.sh
```

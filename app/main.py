import asyncio
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import desc, or_

from app.config import settings
from app.database import engine, Base, get_db
from app.models import Source, Article, StoryCluster
from app.schemas import SourceOut, StoryClusterOut, ArticleOut, PaginatedClustersOut
from app.services.poller import poll_all_sources
from app.services.enrichment import enrich_cluster_with_ai

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION
    }

@app.get(f"{settings.API_V1_STR}/sources", response_model=List[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Source))
    return result.scalars().all()

@app.post(f"{settings.API_V1_STR}/ingest/poll")
async def trigger_poll(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    background_tasks.add_task(poll_all_sources, db)
    return {"message": "Ingestion polling triggered in background."}

@app.post(f"{settings.API_V1_STR}/clusters/{{cluster_id}}/enrich")
async def enrich_cluster_endpoint(cluster_id: int, db: AsyncSession = Depends(get_db)):
    query = (
        select(StoryCluster)
        .where(StoryCluster.id == cluster_id)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    result = await db.execute(query)
    cluster = result.scalar_one_or_none()
    if not cluster:
        raise HTTPException(status_code=404, detail="Story cluster not found")

    enriched = await enrich_cluster_with_ai(db, cluster)
    return {"message": "Cluster enriched successfully", "data": enriched}

@app.get(f"{settings.API_V1_STR}/search", response_model=PaginatedClustersOut)
async def search_story_clusters(
    q: str = Query(..., min_length=2, description="Search query string across headlines and summaries"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    pattern = f"%{q}%"
    query = (
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(
            or_(
                StoryCluster.headline.ilike(pattern),
                StoryCluster.summary.ilike(pattern)
            )
        )
        .order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
    )

    if cursor:
        query = query.where(StoryCluster.id < cursor)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted_clusters = [
        StoryClusterOut(
            id=cluster.id,
            headline=cluster.headline,
            summary=cluster.summary,
            article_count=cluster.article_count,
            first_seen_at=cluster.first_seen_at,
            last_updated_at=cluster.last_updated_at,
            entities=cluster.entities,
            topics=cluster.topics,
            framing_comparison=cluster.framing_comparison,
            articles=[
                ArticleOut(
                    id=art.id,
                    source_id=art.source_id,
                    source_name=art.source.name if art.source else "Unknown",
                    url=art.url,
                    title=art.title,
                    snippet=art.snippet,
                    content=art.content,
                    author=art.author,
                    published_at=art.published_at,
                    image_url=art.image_url
                )
                for art in cluster.articles
            ]
        )
        for cluster in items
    ]

    return PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )

@app.get(f"{settings.API_V1_STR}/clusters", response_model=PaginatedClustersOut)
async def list_story_clusters(
    category: Optional[str] = Query(None, description="Category filter (national, business, official, northeast)"),
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[int] = Query(None, description="Cursor for pagination"),
    db: AsyncSession = Depends(get_db)
):
    query = (
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .order_by(desc(StoryCluster.last_updated_at), desc(StoryCluster.id))
    )

    if category and category.lower() != "all":
        subquery = (
            select(Article.cluster_id)
            .join(Source)
            .where(Source.category == category.lower())
        )
        query = query.where(StoryCluster.id.in_(subquery))

    if cursor:
        query = query.where(StoryCluster.id < cursor)

    query = query.limit(limit + 1)
    result = await db.execute(query)
    clusters = result.scalars().all()

    has_more = len(clusters) > limit
    items = clusters[:limit]
    next_cursor = str(items[-1].id) if has_more and items else None

    formatted_clusters = []
    seen_ids = set()
    for cluster in items:
        if cluster.id in seen_ids:
            continue
        seen_ids.add(cluster.id)

        articles_out = [
            ArticleOut(
                id=art.id,
                source_id=art.source_id,
                source_name=art.source.name if art.source else "Unknown",
                url=art.url,
                title=art.title,
                snippet=art.snippet,
                content=art.content,
                author=art.author,
                published_at=art.published_at,
                image_url=art.image_url
            )
            for art in cluster.articles
        ]
        
        formatted_clusters.append(
            StoryClusterOut(
                id=cluster.id,
                headline=cluster.headline,
                summary=cluster.summary,
                article_count=cluster.article_count,
                first_seen_at=cluster.first_seen_at,
                last_updated_at=cluster.last_updated_at,
                entities=cluster.entities,
                topics=cluster.topics,
                framing_comparison=cluster.framing_comparison,
                articles=articles_out
            )
        )

    return PaginatedClustersOut(
        items=formatted_clusters,
        next_cursor=next_cursor,
        has_more=has_more
    )

@app.get(f"{settings.API_V1_STR}/clusters/{{cluster_id}}", response_model=StoryClusterOut)
async def get_story_cluster(cluster_id: int, db: AsyncSession = Depends(get_db)):
    query = (
        select(StoryCluster)
        .where(StoryCluster.id == cluster_id)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
    )
    result = await db.execute(query)
    cluster = result.scalar_one_or_none()

    if not cluster:
        raise HTTPException(status_code=404, detail="Story cluster not found")

    articles_out = [
        ArticleOut(
            id=art.id,
            source_id=art.source_id,
            source_name=art.source.name if art.source else "Unknown",
            url=art.url,
            title=art.title,
            snippet=art.snippet,
            content=art.content,
            author=art.author,
            published_at=art.published_at,
            image_url=art.image_url
        )
        for art in cluster.articles
    ]

    return StoryClusterOut(
        id=cluster.id,
        headline=cluster.headline,
        summary=cluster.summary,
        article_count=cluster.article_count,
        first_seen_at=cluster.first_seen_at,
        last_updated_at=cluster.last_updated_at,
        entities=cluster.entities,
        topics=cluster.topics,
        framing_comparison=cluster.framing_comparison,
        articles=articles_out
    )

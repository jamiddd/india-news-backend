from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, BigInteger, DateTime, ForeignKey, Index, JSON, Boolean
)
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now():
    return datetime.now(timezone.utc)

class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, index=True, nullable=False)
    feed_url = Column(Text, nullable=False, unique=True)
    homepage_url = Column(Text, nullable=True)
    language = Column(String(10), default="en")
    category = Column(String(50), default="general")
    region = Column(String(50), default="national")
    
    poll_interval_seconds = Column(Integer, default=300)
    etag = Column(String(255), nullable=True)
    last_modified = Column(String(255), nullable=True)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    consecutive_failures = Column(Integer, default=0)
    status = Column(String(50), default="active")  # active, degraded, disabled

    articles = relationship("Article", back_populates="source", cascade="all, delete-orphan")


class StoryCluster(Base):
    __tablename__ = "story_clusters"

    id = Column(Integer, primary_key=True, index=True)
    representative_article_id = Column(Integer, ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)
    headline = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    article_count = Column(Integer, default=1)
    
    first_seen_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    last_updated_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    
    entities = Column(JSON, nullable=True)  # {"persons": [], "organizations": [], "locations": []}
    topics = Column(JSON, nullable=True)    # ["politics", "economy"]
    framing_comparison = Column(JSON, nullable=True) # Outlet headline angle comparison

    articles = relationship("Article", back_populates="cluster", foreign_keys="Article.cluster_id")


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), unique=True, index=True, nullable=False)  # SHA-256 canonical hash
    
    title = Column(Text, nullable=False)
    snippet = Column(Text, nullable=True)
    content = Column(Text, nullable=True)  # Full article body, scraped from the article URL
    author = Column(String(255), nullable=True)
    
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), default=utc_now)
    
    image_url = Column(Text, nullable=True)
    categories = Column(JSON, nullable=True)
    
    simhash = Column(BigInteger, nullable=True, index=True)
    cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="SET NULL"), nullable=True, index=True)

    source = relationship("Source", back_populates="articles")
    cluster = relationship("StoryCluster", back_populates="articles", foreign_keys=[cluster_id])


# Indexes as defined in handoff doc
Index("idx_articles_published_at", Article.published_at.desc())
Index("idx_articles_source_published", Article.source_id, Article.published_at.desc())
Index("idx_clusters_last_updated", StoryCluster.last_updated_at.desc())

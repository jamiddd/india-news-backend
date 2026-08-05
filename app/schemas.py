from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict

class SourceOut(BaseModel):
    id: int
    name: str
    slug: str
    feed_url: str
    homepage_url: Optional[str] = None
    language: str
    category: str
    region: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class ArticleOut(BaseModel):
    id: int
    source_id: int
    source_name: str
    url: str
    title: str
    snippet: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class StoryClusterOut(BaseModel):
    id: int
    headline: str
    summary: Optional[str] = None
    article_count: int
    first_seen_at: datetime
    last_updated_at: datetime
    entities: Optional[Any] = None
    topics: Optional[Any] = None
    framing_comparison: Optional[Any] = None
    articles: List[ArticleOut] = []

    model_config = ConfigDict(from_attributes=True)


class PaginatedClustersOut(BaseModel):
    items: List[StoryClusterOut]
    next_cursor: Optional[str] = None
    has_more: bool

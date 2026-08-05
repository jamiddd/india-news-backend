from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict, Field

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
    content: Optional[str] = None
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


class UserPreferences(BaseModel):
    theme_mode: str = "system"
    accent_color: str = "blue"
    language_pref: str = "all"
    enabled_categories: List[str] = Field(default_factory=list)
    custom_categories: List[str] = Field(default_factory=list)


class UserAuthRequest(BaseModel):
    email: str
    display_name: str
    provider: str
    uid: Optional[str] = None


class UserAuthResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    token: Optional[str] = None
    preferences: UserPreferences


class CommunityPostCreate(BaseModel):
    user_id: str
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=3, max_length=10000)
    category: str = Field(min_length=1, max_length=80)
    image_urls: List[str] = Field(default_factory=list, max_length=10)
    submit_for_review: bool = True


class CommunityPostOut(BaseModel):
    id: int
    author_id: str
    author_display_name: str
    title: str
    body: str
    category: str
    image_urls: List[str] = []
    status: str
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class CommunityPostModeration(BaseModel):
    admin_user_id: str
    rejection_reason: Optional[str] = None


class CommunityPostUpdate(BaseModel):
    user_id: str
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=3, max_length=10000)
    category: str = Field(min_length=1, max_length=80)
    image_urls: List[str] = Field(default_factory=list, max_length=10)
    submit_for_review: bool = True


class CommunityPostReportCreate(BaseModel):
    user_id: str
    reason: str = Field(min_length=2, max_length=40)
    details: Optional[str] = Field(default=None, max_length=2000)


class CommunityPostReportOut(BaseModel):
    id: int
    post_id: int
    reporter_id: str
    reason: str
    details: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

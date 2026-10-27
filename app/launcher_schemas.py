"""Response models for the Spot launcher feed (GET /api/v1/launcher/feed).

Kept apart from app/schemas.py so the launcher contract is isolated from the
News app's. Items are a discriminated union on `type` so new card kinds
(game tiles, more promos) can be added without breaking old launcher builds,
which must ignore types they do not know.
"""
from datetime import datetime
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class LauncherAngle(BaseModel):
    outlet: str
    headline_angle: str


class LauncherTeaser(BaseModel):
    """The free sample of the News app's outlet comparison: how many outlets
    cover the story, and one outlet's angle. Never the full comparison."""
    source_count: int
    angle: LauncherAngle


class LauncherStoryItem(BaseModel):
    type: Literal["story"] = "story"
    id: str
    cluster_id: int
    tag: str
    source_name: str
    title: str
    published_at: datetime
    image_url: Optional[str] = None
    teaser: Optional[LauncherTeaser] = None


class LauncherCta(BaseModel):
    label: str
    # "news_app": the launcher opens News if installed, else the Play Store.
    target: Literal["news_app"] = "news_app"
    deeplink: Optional[str] = None
    # Install-referrer attribution: utm_campaign / utm_content for the store link.
    campaign: str
    content: Optional[str] = None


class LauncherPromoItem(BaseModel):
    type: Literal["promo"] = "promo"
    id: str
    variant: str = "A"
    title: str
    body: str
    dismissible: bool = True
    cta: LauncherCta


class LauncherGameTileItem(BaseModel):
    """Today's daily puzzle. `cta.deeplink` is the News app App Link
    (https://openindiannews.com/games/{game}/daily) used when News is installed;
    otherwise the launcher sends the user to the Play Store with `cta.campaign`
    and `cta.content` (game_<game>) in the install referrer."""
    type: Literal["game_tile"] = "game_tile"
    id: str
    game: str
    title: str
    subtitle: str
    dismissible: bool = True
    cta: LauncherCta


LauncherFeedItem = Annotated[
    Union[LauncherStoryItem, LauncherPromoItem, LauncherGameTileItem], Field(discriminator="type")
]


class LauncherFeedOut(BaseModel):
    items: List[LauncherFeedItem]
    next_cursor: Optional[str] = None
    has_more: bool
    generated_at: datetime

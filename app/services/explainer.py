"""Builds one Explainer: have Claude write the answer, optionally voice it
with Sarvam, and store the result on the Explainer row.

Run by the admin "Generate"/"Regenerate" buttons in admin_explainers.py, via
BackgroundTasks — there is no scheduled cycle (unlike Daily Brief/Timelines),
since every explainer starts from an admin-posed question rather than a fixed
daily selection.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models import Article, Explainer, StoryCluster
from app.redis_client import get_redis_client
from app.services import explainer_script, timeline_audio
from app.services.job_lease import job_lease

logger = logging.getLogger(__name__)

CACHE_KEY_LIST = "explainers:list:v1"
LEASE_JOB_PREFIX = "explainer"
LEASE_TTL_SECONDS = 10 * 60
STATUS_TTL_SECONDS = 24 * 3600
RUNNING_TTL_SECONDS = 10 * 60

# Rough, told-to-the-admin cost estimate (Claude Sonnet call plus, if narrated,
# a Sarvam TTS call) — not billed/metered precisely, matching the Daily
# Brief admin page's "roughly Rs 8" framing rather than an exact ledger.
BASE_GENERATION_COST = 7.0
NARRATION_COST = 3.0


def _lease_job(explainer_id: int) -> str:
    return f"{LEASE_JOB_PREFIX}:{explainer_id}"


def _status_key(explainer_id: int) -> str:
    return f"explainer_status:{explainer_id}"


async def set_status(explainer_id: int, state: str, message: str = "") -> None:
    payload = json.dumps({"state": state, "message": message, "at": datetime.now(timezone.utc).isoformat()})
    ttl = RUNNING_TTL_SECONDS if state == "generating" else STATUS_TTL_SECONDS
    try:
        await get_redis_client().setex(_status_key(explainer_id), ttl, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not record explainer status for %s: %s", explainer_id, e)


async def get_status(explainer_id: int) -> dict:
    try:
        value = await get_redis_client().get(_status_key(explainer_id))
    except Exception as e:  # noqa: BLE001
        logger.warning("could not read explainer status: %s", e)
        return {}
    return json.loads(value) if value else {}


async def in_progress(explainer_id: int) -> bool:
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as s:
            row = await s.execute(
                text("SELECT 1 FROM job_lease WHERE job_name = :job AND expires_at > now()"),
                {"job": _lease_job(explainer_id)},
            )
            return row.first() is not None
    except Exception as e:  # noqa: BLE001
        logger.warning("could not check explainer lease for %s: %s", explainer_id, e)
        return False


async def invalidate_list_cache() -> None:
    try:
        await get_redis_client().delete(CACHE_KEY_LIST)
    except Exception:  # noqa: BLE001
        pass


# Matches main.py's get_explainer()'s own `cache_key` construction exactly —
# duplicated here (not imported) since main.py doesn't expose it as a
# reusable name; keep both in sync if that format ever changes.
def _detail_cache_key(explainer_id: int) -> str:
    return f"explainer:{explainer_id}:v1"


async def invalidate_detail_cache(explainer_id: int) -> None:
    """Was missing (2026-09-28 bug, caught backfilling source attribution):
    invalidate_list_cache() only ever busted the feed LIST cache, never a
    single explainer's own GET /explainers/{id} cache — so publish/archive/
    restore, and any backfill script that edits a row directly, could leave
    a stale detail response serving for up to CACHE_TTL_SECONDS after the
    row actually changed."""
    try:
        await get_redis_client().delete(_detail_cache_key(explainer_id))
    except Exception:  # noqa: BLE001
        pass


async def _update(explainer_id: int, **fields) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()


async def add_source(explainer_id: int, cluster_id: int) -> None:
    """Attach a real StoryCluster as one of this explainer's sources — see
    the admin source picker (admin_explainers.py). No-op if already attached
    or the explainer doesn't exist."""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            return
        ids = list(row.source_cluster_ids or [])
        if cluster_id not in ids:
            ids.append(cluster_id)
            row.source_cluster_ids = ids
            await session.commit()


async def remove_source(explainer_id: int, cluster_id: int) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            return
        row.source_cluster_ids = [i for i in (row.source_cluster_ids or []) if i != cluster_id]
        await session.commit()


async def fetch_source_clusters(cluster_ids: list[int]) -> list[StoryCluster]:
    """The real StoryCluster rows for `cluster_ids`, articles+sources
    eager-loaded, in the same order as `cluster_ids` — an id whose cluster
    has since been deleted is silently dropped rather than failing the
    whole fetch."""
    if not cluster_ids:
        return []
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(StoryCluster)
                .where(StoryCluster.id.in_(cluster_ids))
                .options(selectinload(StoryCluster.articles).selectinload(Article.source))
            )
        ).scalars().all()
    by_id = {c.id: c for c in rows}
    return [by_id[i] for i in cluster_ids if i in by_id]


def _cluster_to_excerpt(cluster: StoryCluster) -> "explainer_script.SourceExcerpt":
    outlets: list[str] = []
    seen: set[str] = set()
    for article in cluster.articles:
        name = article.source.name if article.source else None
        if name and name not in seen:
            seen.add(name)
            outlets.append(name)
    return explainer_script.SourceExcerpt(
        cluster_id=cluster.id,
        headline=cluster.headline,
        summary=cluster.summary or "",
        distinct_source_count=cluster.distinct_source_count or len(outlets),
        outlets=outlets[:6],
        article_titles=[a.title for a in cluster.articles[:3]],
    )


def derive_hero_image(clusters: list[StoryCluster]) -> Optional[str]:
    """The feed/detail hero photo for this explainer — the same
    representative-article image its trigger cluster's own feed card would
    show, tried cluster by cluster (in `clusters`' order, so the trigger
    story wins) until one has an image. None if none of the attached
    clusters have one; there is no admin override for this yet, so
    regenerating always recomputes it fresh from the current sources."""
    for cluster in clusters:
        article = next(
            (a for a in cluster.articles if a.id == cluster.representative_article_id),
            cluster.articles[0] if cluster.articles else None,
        )
        if article and article.image_url:
            return article.image_url
    return None


def cluster_to_source(cluster: StoryCluster) -> dict:
    """The real, verifiable source entry served to the app for this cluster
    — a representative article's title/outlet/url, never anything Claude
    said. Prefers the cluster's own representative_article_id (the same
    article the rest of the app treats as this story's lead).

    Also carries `source_count`/`outlet_names`/`outlet_urls` (2026-09-28,
    added after the app's first citation-list design turned out misleading):
    tapping a source deep-links to the full StoryCluster (openExplainerSource
    -> selectCluster), the same multi-outlet aggregated story page any feed
    card opens to — never a single publisher's own article page. A UI built
    from just `outlet`/`url` alone (one favicon, one name) implied the
    opposite. These extra fields let the app show the same "{Source1},
    {Source2} and N others" + stacked-favicon treatment FeedNewsItemProduction
    already uses for the identical fact (a story is corroborated by multiple
    outlets), instead of inventing a different-looking pattern for the same
    underlying reality."""
    article = next(
        (a for a in cluster.articles if a.id == cluster.representative_article_id),
        cluster.articles[0] if cluster.articles else None,
    )
    outlet_names: list[str] = []
    outlet_urls: list[str] = []
    seen_source_ids: set[int] = set()
    for a in cluster.articles:
        if a.source_id in seen_source_ids or not a.source:
            continue
        seen_source_ids.add(a.source_id)
        outlet_names.append(a.source.name)
        outlet_urls.append(a.url)
        if len(outlet_names) == 2:
            break
    if article is None:
        return {
            "title": cluster.headline, "outlet": "", "url": None, "cluster_id": cluster.id,
            "source_count": cluster.distinct_source_count or 0, "outlet_names": outlet_names, "outlet_urls": outlet_urls,
        }
    return {
        "title": article.title,
        "outlet": article.source.name if article.source else "",
        "url": article.url,
        "cluster_id": cluster.id,
        "source_count": cluster.distinct_source_count or len(seen_source_ids),
        "outlet_names": outlet_names,
        "outlet_urls": outlet_urls,
    }


async def run_build_task(explainer_id: int, *, narrate: bool = False, voice: Optional[str] = None) -> None:
    """build_explainer as a fire-and-forget task (the admin's Generate
    button): whatever happens, status ends as ready_for_review/error, never
    stuck on "generating"."""
    try:
        await build_explainer(explainer_id, narrate=narrate, voice=voice)
    except Exception as exc:  # noqa: BLE001
        logger.exception("explainer %s crashed", explainer_id)
        await _update(explainer_id, status="draft", error=f"{type(exc).__name__}: {exc}")
        await set_status(explainer_id, "error", str(exc))


async def build_explainer(explainer_id: int, *, narrate: bool = False, voice: Optional[str] = None) -> bool:
    """Generate (or regenerate) the full explainer. Returns True on success.
    Never raises for an expected failure — records status/error and returns
    False; unexpected exceptions propagate to run_build_task's handler."""
    async with job_lease(_lease_job(explainer_id), LEASE_TTL_SECONDS) as held:
        if not held:
            await set_status(explainer_id, "error", "another generation is already running")
            return False

        async with AsyncSessionLocal() as session:
            row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
        if row is None:
            await set_status(explainer_id, "error", "explainer not found")
            return False

        source_ids = list(row.source_cluster_ids or [])
        if not source_ids:
            await _update(explainer_id, status="draft", error="Attach at least one source before generating")
            await set_status(explainer_id, "error", "attach at least one source before generating")
            return False

        clusters = await fetch_source_clusters(source_ids)
        if not clusters:
            await _update(explainer_id, status="draft", error="Attached sources no longer exist")
            await set_status(explainer_id, "error", "attached sources no longer exist")
            return False

        await set_status(explainer_id, "generating", "writing answer")
        await _update(explainer_id, status="generating", error=None)

        excerpts = [_cluster_to_excerpt(c) for c in clusters]
        answer = await explainer_script.write_explainer(row.question, row.category, row.depth, row.admin_notes, excerpts)
        if answer is None:
            await _update(explainer_id, status="draft", error="Claude did not return a valid explainer")
            await set_status(explainer_id, "error", "Claude did not return a valid explainer")
            return False

        derived_sources = [cluster_to_source(c) for c in clusters]
        derived_hero_image = derive_hero_image(clusters)

        audio_fields: dict = {"audio_url": None, "audio_duration_seconds": None, "voice": None}
        cost = BASE_GENERATION_COST
        if narrate and timeline_audio.is_configured():
            await set_status(explainer_id, "generating", "voicing")
            speaker = voice or "shubh"
            chunks = [answer["quick_answer"]] + [s["body"] for s in answer["sections"]]
            rendered = await timeline_audio.render_and_upload(
                f"explainer {explainer_id}", chunks, 0,
                f"explainers/{explainer_id}.{timeline_audio.AUDIO_FILE_EXTENSION}",
                speaker=speaker,
            )
            if rendered is not None:
                url, duration_seconds, _offsets = rendered
                audio_fields = {"audio_url": url, "audio_duration_seconds": duration_seconds, "voice": speaker}
                cost += NARRATION_COST
            else:
                logger.warning("explainer %s: narration requested but voicing failed; publishing without audio", explainer_id)
        elif narrate:
            logger.warning("explainer %s: narration requested but audio is not configured", explainer_id)

        await _update(
            explainer_id,
            status="ready_for_review",
            quick_answer=answer["quick_answer"],
            sections=answer["sections"],
            sources=derived_sources,
            hero_image_url=derived_hero_image,
            generation_cost=cost,
            error=None,
            **audio_fields,
        )
        # A "Regenerate all" on an already-PUBLISHED explainer would
        # otherwise leave its stale pre-regeneration content serving from
        # cache for up to CACHE_TTL_SECONDS — see invalidate_detail_cache's
        # own docstring for the rest of this bug class.
        await invalidate_detail_cache(explainer_id)
        await set_status(explainer_id, "done", "ready for review")
        return True


async def regenerate_section(explainer_id: int, section_index: int) -> bool:
    """Regenerate one section's body in place, keeping its heading and
    leaving quick_answer/sources/audio untouched. Grounded in the same
    attached sources as the original generation."""
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
    if row is None or not row.sections or not (0 <= section_index < len(row.sections)):
        return False

    clusters = await fetch_source_clusters(list(row.source_cluster_ids or []))
    if not clusters:
        return False

    heading = row.sections[section_index]["heading"]
    excerpts = [_cluster_to_excerpt(c) for c in clusters]
    body = await explainer_script.write_section(row.question, row.category, heading, row.admin_notes, excerpts)
    if body is None:
        return False

    sections = list(row.sections)
    sections[section_index] = {"heading": heading, "body": body}
    await _update(explainer_id, sections=sections)
    await invalidate_detail_cache(explainer_id)
    return True


async def publish(explainer_id: int) -> None:
    await _update(explainer_id, status="published", published_at=datetime.now(timezone.utc))
    await invalidate_list_cache()
    await invalidate_detail_cache(explainer_id)


async def archive(explainer_id: int) -> None:
    await _update(explainer_id, status="archived")
    await invalidate_list_cache()
    await invalidate_detail_cache(explainer_id)


async def restore(explainer_id: int) -> None:
    """Back to published — restoring an archived explainer republishes it
    rather than returning it to draft, matching what "Restore" implies on
    the dashboard (see the approved Explainers design mockup)."""
    await _update(explainer_id, status="published")
    await invalidate_list_cache()
    await invalidate_detail_cache(explainer_id)

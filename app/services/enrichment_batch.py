"""Batch API path for enrichment refinement passes.

Enrichment is background work — nothing in the app is blocked on it — so the
refinement passes can run through Anthropic's Message Batches API at 50% of
list price. That halving stacks with every other cost lever in
docs/multi-source-feed-plan.md, which is why §5.G makes it part of the plan
rather than a follow-up.

WHAT DOES *NOT* COME HERE. The 1->2 crossing — a story entering the feed —
stays synchronous. A batch turns around in minutes usually, but is allowed
24 hours, and a corroborated story showing its raw RSS headline for a day is
exactly the failure the multi-source feed exists to prevent. The split is
made on StoryCluster.last_enriched_at (never enriched => synchronous), not
on ai_enriched, which the poller resets for both cases alike.

Batches outlive the process that submits them, so this is a two-phase
protocol across timer ticks: submit_refinement_batch() posts and records,
reconcile_open_batches() applies whatever has finished since. Raw HTTP via
httpx, matching enrichment.py — the request body is built by the same
build_enrichment_request() the synchronous path uses, so the two can't drift.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

import httpx
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Article, EnrichmentBatch, StoryCluster, utc_now
from app.services.enrichment import (
    apply_baseline_enrichment,
    apply_ai_response,
    build_enrichment_request,
)

logger = logging.getLogger(__name__)

API_ROOT = "https://api.anthropic.com/v1/messages/batches"

# A batch is capped at 100,000 requests, far above anything this app
# produces (~350 multi-source clusters/day, of which refinements are a
# fraction). This cap is about blast radius, not API limits: a bug that
# selects the wrong rows costs one batch, not the whole table.
MAX_REQUESTS_PER_BATCH = 500

# Results are retrievable for 29 days. A batch still unfinished well beyond
# the API's own 24-hour ceiling is stuck, not slow — stop polling it so one
# bad row can't be retried forever.
BATCH_STALE_HOURS = 30


def _headers() -> Dict[str, str]:
    return {
        "x-api-key": settings.ANTHROPIC_API_KEY or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


async def submit_refinement_batch(
    session, clusters: Sequence[StoryCluster]
) -> Optional[str]:
    """Submit refinement enrichments for `clusters`. Returns the batch id.

    The rule-based baseline is applied and committed here, before the batch
    is submitted, for the same reason the synchronous path applies it first:
    it is free, and it must be on the row whether or not the paid pass ever
    lands.

    This no longer blanks framing_comparison. Committing a null here and only
    filling it back in when the batch returned meant the row served no framing
    for the whole turnaround, which hit high-coverage stories hardest because
    they refine most often — see the comment in
    app.services.enrichment.apply_baseline_enrichment. The previous comparison
    now survives until this batch overwrites it, and read-path age-gating
    (app.main._framing_for_response) bounds how stale it can get.
    """
    if not settings.ANTHROPIC_API_KEY or not clusters:
        return None

    clusters = list(clusters)[:MAX_REQUESTS_PER_BATCH]
    requests: List[Dict[str, Any]] = []
    for cluster in clusters:
        can_compare_framing = apply_baseline_enrichment(cluster)
        requests.append(
            {
                # The cluster id IS the correlation key. Results come back in
                # arbitrary order, so nothing may be matched by position.
                "custom_id": f"cluster-{cluster.id}",
                "params": build_enrichment_request(cluster, can_compare_framing),
            }
        )
    await session.commit()

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            API_ROOT, headers=_headers(), json={"requests": requests}
        )
        resp.raise_for_status()
        data = resp.json()

    batch_id = data["id"]
    session.add(
        EnrichmentBatch(
            batch_id=batch_id,
            status="in_progress",
            request_count=len(requests),
        )
    )
    await session.commit()
    logger.info(
        f"[enrich-batch] submitted {batch_id} with {len(requests)} request(s)"
    )
    return batch_id


async def _fetch_results(client: httpx.AsyncClient, results_url: str) -> List[Dict[str, Any]]:
    """Read a finished batch's results file (JSON Lines, one per request)."""
    resp = await client.get(results_url, headers=_headers())
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


async def _apply_batch_results(session, results: List[Dict[str, Any]]) -> Dict[str, int]:
    """Write a finished batch's results onto their clusters."""
    by_id: Dict[int, Dict[str, Any]] = {}
    for row in results:
        custom_id = row.get("custom_id") or ""
        if not custom_id.startswith("cluster-"):
            logger.warning(f"[enrich-batch] unrecognised custom_id {custom_id!r}")
            continue
        try:
            by_id[int(custom_id[len("cluster-"):])] = row
        except ValueError:
            logger.warning(f"[enrich-batch] unparseable custom_id {custom_id!r}")

    if not by_id:
        return {"succeeded": 0, "errored": 0}

    res = await session.execute(
        select(StoryCluster)
        .options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(StoryCluster.id.in_(list(by_id.keys())))
    )
    clusters = {c.id: c for c in res.scalars().all()}

    succeeded = errored = 0
    for cluster_id, row in by_id.items():
        cluster = clusters.get(cluster_id)
        if cluster is None:
            # Deleted or merged away while the batch was in flight. Not an
            # error worth alarming on — just nothing to write to.
            continue
        result = row.get("result") or {}
        if result.get("type") != "succeeded":
            errored += 1
            logger.warning(
                f"[enrich-batch] cluster #{cluster_id} "
                f"{result.get('type')}: {result.get('error')}"
            )
            continue
        try:
            # Recomputed rather than remembered from submission time: the
            # cluster may have gained an outlet while the batch ran, and
            # framing eligibility must reflect the row as it is now.
            can_compare_framing = (
                len({a.source_id for a in (cluster.articles or []) if a.source_id})
                >= 2
            )
            apply_ai_response(cluster, result["message"], can_compare_framing)
            succeeded += 1
        except Exception as e:
            # One malformed response must not cost the rest of the batch.
            # The baseline written at submission time stays in place, and so
            # does the cluster's previous framing comparison — the read path
            # expires it on age if no later pass ever refreshes it.
            errored += 1
            logger.warning(
                f"[enrich-batch] cluster #{cluster_id} response unusable: {e}"
            )

    await session.commit()
    return {"succeeded": succeeded, "errored": errored}


async def reconcile_open_batches(session) -> Dict[str, int]:
    """Poll every in-progress batch and apply the ones that have finished.

    Returns counters for logging. Safe to call on every timer tick — a batch
    still running is left alone, and the unique constraint on batch_id plus
    the status transition mean a result set is applied at most once.
    """
    if not settings.ANTHROPIC_API_KEY:
        return {"checked": 0, "ended": 0, "succeeded": 0, "errored": 0}

    res = await session.execute(
        select(EnrichmentBatch).where(EnrichmentBatch.status == "in_progress")
    )
    open_batches = res.scalars().all()
    totals = {"checked": len(open_batches), "ended": 0, "succeeded": 0, "errored": 0}

    async with httpx.AsyncClient(timeout=60.0) as client:
        for batch in open_batches:
            try:
                resp = await client.get(
                    f"{API_ROOT}/{batch.batch_id}", headers=_headers()
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(
                    f"[enrich-batch] could not poll {batch.batch_id}: {e}"
                )
                continue

            if data.get("processing_status") != "ended":
                age_hours = (
                    utc_now() - batch.created_at
                ).total_seconds() / 3600.0
                if age_hours > BATCH_STALE_HOURS:
                    batch.status = "expired"
                    batch.reconciled_at = utc_now()
                    logger.warning(
                        f"[enrich-batch] {batch.batch_id} still running after "
                        f"{age_hours:.0f}h — giving up on it"
                    )
                continue

            results_url = data.get("results_url")
            if not results_url:
                batch.status = "expired"
                batch.reconciled_at = utc_now()
                logger.warning(
                    f"[enrich-batch] {batch.batch_id} ended with no results_url"
                )
                continue

            results = await _fetch_results(client, results_url)
            applied = await _apply_batch_results(session, results)

            batch.status = "ended"
            batch.succeeded_count = applied["succeeded"]
            batch.errored_count = applied["errored"]
            batch.reconciled_at = utc_now()
            totals["ended"] += 1
            totals["succeeded"] += applied["succeeded"]
            totals["errored"] += applied["errored"]
            logger.info(
                f"[enrich-batch] {batch.batch_id} applied: "
                f"{applied['succeeded']} ok, {applied['errored']} failed"
            )

    await session.commit()
    return totals

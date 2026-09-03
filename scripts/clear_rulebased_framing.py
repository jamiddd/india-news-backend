"""
One-off repair: clear every framing_comparison that was produced by the
rule-based fallback rather than by a real model call, and queue those
clusters for genuine re-enrichment.

The code fix (generate_framing_comparison deleted from enrichment.py) stops
NEW fabrications. It does nothing for rows already written, which is what
users are actually looking at — observed in production 2026-09-03: a 4-outlet
Tibet earthquake story where all four outlets were labelled the identical
"Disaster / Casualty Report", and a 5-outlet Tamil Nadu story where every
"angle" was just the first five words of that outlet's own headline.

DETECTION — this is exact, not a heuristic, unlike
scripts/add_ai_enriched_column.py's label-matching backfill:

    generate_framing_comparison() emitted three keys per entry —
    {"outlet", "headline", "headline_angle"}.

    ENRICHMENT_SYSTEM_PROMPT's declared output format has only two —
    {"outlet", "headline_angle"}.

So the presence of a "headline" key on a framing entry means that row came
from the rule-based path. The model was never asked for that field and has
no reason to invent it. This also correctly catches rows whose angle text
happens to look model-written (the first-five-words fallback), which the
fixed-label matching in add_ai_enriched_column.py cannot see at all.

Cleared rows also get ai_enriched=FALSE so the normal enrich timer picks
them up and produces a real comparison. Only clusters still inside the
timer's window will actually be revisited; older ones simply stop showing a
fabricated "Media framing" section, which is the point — the client already
hides the section when framing_comparison is null.

CONNECTIONS — uses app.database.admin_engine(), not the module-level engine.
That engine is sized for the long-lived web app; a one-off script that
borrowed it just queued behind the app and died with:

    asyncpg.exceptions.InternalServerError: (EMAXCONNSESSION)
    max clients reached in session mode

admin_engine() gives a NullPool connection instead, and — equally important
— carries the pgbouncer connect args when DB_PGBOUNCER is set. A script that
hand-rolled its own create_async_engine() silently lacked those and failed
against the transaction pooler with "prepared statement ... does not exist".

Usage:
    python3 scripts/clear_rulebased_framing.py           # apply
    python3 scripts/clear_rulebased_framing.py --dry-run # count only

    # if the session pooler is saturated:
    ADMIN_DATABASE_URL='postgresql+asyncpg://...:5432/postgres' \
        python3 scripts/clear_rulebased_framing.py --dry-run
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import admin_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# jsonb_typeof guard first: jsonb_array_elements raises on a non-array value
# and would abort the whole set-based statement. Same guard, same reason, as
# add_ai_enriched_column.py.
MATCH_PREDICATE = """
    framing_comparison IS NOT NULL
    AND jsonb_typeof(framing_comparison::jsonb) = 'array'
    AND EXISTS (
        SELECT 1
        FROM jsonb_array_elements(framing_comparison::jsonb) AS elem
        -- jsonb_exists(elem, 'headline') is the function form of the `?`
        -- key-exists operator. Spelled out because a bare `?` inside a
        -- text() string is ambiguous with DBAPI paramstyle placeholders.
        WHERE jsonb_exists(elem, 'headline')
    )
"""


async def main(dry_run: bool) -> None:
    engine = admin_engine()
    try:
        await _run(engine, dry_run)
    finally:
        # NullPool holds nothing, but dispose() still releases the connection
        # promptly rather than at interpreter exit — this is running against a
        # pooler with 15 slots and no headroom.
        await engine.dispose()


async def _run(engine, dry_run: bool) -> None:
    async with engine.begin() as conn:
        count = (await conn.execute(text(
            f"SELECT COUNT(*) FROM story_clusters WHERE {MATCH_PREDICATE}"
        ))).scalar_one()
        logger.info(f"{count} cluster(s) carry rule-based framing_comparison.")

        if dry_run:
            # ai_enriched is the diagnostic that separates the two competing
            # explanations for how rule-based framing reached users:
            #   TRUE  -> the Anthropic call SUCCEEDED but its JSON omitted the
            #            framing_comparison key, so enrichment.py's
            #            `elif "framing_comparison" in structured` never fired
            #            and the rule-based baseline survived. Points at
            #            max_tokens truncation (framing is the last field).
            #   FALSE -> the call failed and the warning logging isn't
            #            reaching us.
            sample = (await conn.execute(text(
                f"SELECT id, ai_enriched, distinct_source_count, headline, "
                f"framing_comparison FROM story_clusters "
                f"WHERE {MATCH_PREDICATE} ORDER BY last_updated_at DESC LIMIT 5"
            ))).fetchall()
            for row in sample:
                logger.info(
                    f"  #{row[0]} ai_enriched={row[1]} outlets={row[2]} "
                    f"{row[3][:60]!r} -> {row[4]}"
                )

            split = (await conn.execute(text(
                f"SELECT ai_enriched, COUNT(*) FROM story_clusters "
                f"WHERE {MATCH_PREDICATE} GROUP BY ai_enriched"
            ))).fetchall()
            for flag, n in split:
                logger.info(f"  ai_enriched={flag}: {n} cluster(s)")
            logger.info("Dry run — nothing written.")
            return

        if not count:
            return

        # NULL, not '[]': app/models.py sets none_as_null=True on this column
        # precisely so a cleared row reads back as SQL NULL and stays visible
        # to `IS NULL` queries. Writing JSON 'null' or an empty array here
        # would defeat that.
        result = await conn.execute(text(f"""
            UPDATE story_clusters
            SET framing_comparison = NULL,
                ai_enriched = FALSE
            WHERE {MATCH_PREDICATE}
        """))
        logger.info(
            f"Cleared framing_comparison and queued re-enrichment for "
            f"{result.rowcount} cluster(s)."
        )


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))

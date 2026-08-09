"""
One-off migration for existing deployments: adds `story_clusters.ai_enriched`
(see app/models.py for what it means and why entities/topics/framing_comparison
being non-null is NOT the same signal) and backfills a best-effort value for
every existing row, since the column can't be derived exactly after the fact.

Backfill logic, in order:
  1. article_count == 1 -> False, definitively. enrich_cluster_with_ai()'s own
     cost guardrail skips the Anthropic call entirely for singleton clusters
     (framing/synthesis is meaningless with one article), so these never had
     a chance to be truly AI-enriched.
  2. article_count >= 2 and framing_comparison is null -> False. Never even
     ran the rule-based baseline, let alone the AI call.
  3. article_count >= 2 and every framing_comparison entry's headline_angle
     is one of the 4 fixed strings generate_framing_comparison() (the rule-
     based fallback) always uses -> False. The AI prompt asks for a
     "descriptive framing comparison" — free-text, not drawn from a fixed
     set — so all-fixed-strings is a strong (not certain) signal the
     Anthropic call never actually ran or was never reached.
  4. Otherwise -> True. At least one framing_comparison entry uses language
     outside that fixed set, which the rule-based path cannot produce.

This is a heuristic for pre-existing rows only — there's a small chance the
model coincidentally output one of the 4 exact fallback phrases, which would
misclassify a real success as False (the safe direction to be wrong in: it
just means that cluster gets needlessly retried once credit is back, not
that a real failure looks like a success). Going forward, ai_enriched is set
exactly (enrichment.py sets it only on a confirmed successful API call), no
heuristics involved.

Usage:
    python3 scripts/add_ai_enriched_column.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text

from app.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RULE_BASED_ANGLES = [
    "Official / Policy Statement",
    "Conflict & Opposition Impact",
    "Financial & Market Impact",
    "General Reporting",
]


async def main():
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE story_clusters ADD COLUMN IF NOT EXISTS ai_enriched BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_clusters_ai_enriched ON story_clusters (ai_enriched)"
        ))
        logger.info("story_clusters.ai_enriched column + index are present.")

        # Singletons: definitively never AI-enriched.
        result = await conn.execute(text(
            "UPDATE story_clusters SET ai_enriched = FALSE WHERE article_count <= 1"
        ))
        logger.info(f"Set ai_enriched=FALSE for {result.rowcount} singleton clusters.")

        # Multi-source clusters: heuristic based on whether every
        # framing_comparison angle is one of the fixed rule-based strings.
        # Presence of at least one entry outside that fixed set is treated
        # as evidence of a genuine AI response. RULE_BASED_ANGLES is a fixed
        # list of literals defined in this file, not user input — safe to
        # inline directly rather than fight SQLAlchemy's IN-clause binding.
        angles_sql = ", ".join("'" + a.replace("'", "''") + "'" for a in RULE_BASED_ANGLES)
        result = await conn.execute(text(f"""
            UPDATE story_clusters
            SET ai_enriched = EXISTS (
                SELECT 1 FROM jsonb_array_elements(framing_comparison::jsonb) AS elem
                WHERE elem->>'headline_angle' NOT IN ({angles_sql})
            )
            WHERE article_count >= 2 AND framing_comparison IS NOT NULL
        """))
        logger.info(f"Backfilled ai_enriched heuristically for {result.rowcount} multi-source clusters.")


if __name__ == "__main__":
    asyncio.run(main())

"""
One-off migration: remove the "science" and "official" (PIB) categories.

Context: both categories were permanently empty in the feed because of the
multi-source feed gate (>= 2 distinct sources required) combined with too
few seeded sources per category — PIB was the sole "official" source
(structurally can never reach 2), and "science" only had 2 sources total.
Decision: drop both as curated categories. Science is now reachable only
via a user's own custom topic search; PIB is dropped as not important.
See scripts/migrations/2026-09-08_remove_science_official_categories.sql
for the equivalent raw SQL (kept for reference/manual running); this script
does the same thing through the app's own DB session so it can be run
inside the prod container with no separate psql/DB-URL setup.

Run inside the running container (uses that container's DATABASE_URL, same
as scripts/seed_sources.py):

    docker compose -f docker-compose.prod.yml exec app \\
        python3 scripts/migrations/2026_09_08_remove_science_official.py

Run on BOTH droplets (newsapp, newsapp-2) if you also run it via -T/exec on
each — but note this only needs to run ONCE total, since both droplets
share the same Supabase database; running it a second time is a harmless
no-op (all statements are idempotent), it's just wasted effort.

Verify-only, no changes:

    docker compose -f docker-compose.prod.yml exec app \\
        python3 scripts/migrations/2026_09_08_remove_science_official.py --check
"""
import asyncio
import sys

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import Source  # noqa: E402

RENAME_TO_TECH = ("indian-express-science", "toi-science")
DISABLE_SLUG = "pib"
STALE_KEYS = ("science", "official")


async def check(session) -> bool:
    """Prints current state; returns True if everything already looks migrated."""
    result = await session.execute(
        select(Source.slug, Source.category, Source.status).where(
            Source.slug.in_(RENAME_TO_TECH + (DISABLE_SLUG,))
        )
    )
    rows = result.all()
    print("-- sources --")
    for slug, category, status in sorted(rows):
        print(f"  {slug:28s} category={category!r:12s} status={status!r}")

    stale = await session.execute(
        text(
            """
            SELECT count(*) FROM users
            WHERE preferences::jsonb -> 'enabled_categories' ?| :keys
               OR preferences::jsonb -> 'custom_categories' ?| :keys
            """
        ),
        {"keys": list(STALE_KEYS)},
    )
    stale_count = stale.scalar_one()
    print(f"-- stale user preference rows (expect 0): {stale_count}")

    tech_ok = all(cat == "tech" for slug, cat, _ in rows if slug in RENAME_TO_TECH)
    pib_ok = all(status == "disabled" for slug, _, status in rows if slug == DISABLE_SLUG)
    return tech_ok and pib_ok and stale_count == 0


async def migrate(session) -> None:
    for slug in RENAME_TO_TECH:
        await session.execute(
            text("UPDATE sources SET category = 'tech' WHERE slug = :slug"),
            {"slug": slug},
        )
    await session.execute(
        text("UPDATE sources SET status = 'disabled' WHERE slug = :slug"),
        {"slug": DISABLE_SLUG},
    )
    await session.execute(
        text(
            """
            UPDATE users
            SET preferences = jsonb_set(
                jsonb_set(
                    preferences::jsonb,
                    '{enabled_categories}',
                    COALESCE(
                        (SELECT jsonb_agg(elem)
                         FROM jsonb_array_elements(preferences::jsonb -> 'enabled_categories') elem
                         WHERE NOT (elem::text = ANY (:quoted_keys))),
                        '[]'::jsonb
                    )
                ),
                '{custom_categories}',
                COALESCE(
                    (SELECT jsonb_agg(elem)
                     FROM jsonb_array_elements(preferences::jsonb -> 'custom_categories') elem
                     WHERE NOT (elem::text = ANY (:quoted_keys))),
                    '[]'::jsonb
                )
            )::json
            WHERE preferences::jsonb -> 'enabled_categories' ?| :keys
               OR preferences::jsonb -> 'custom_categories' ?| :keys
            """
        ),
        {
            "keys": list(STALE_KEYS),
            "quoted_keys": [f'"{k}"' for k in STALE_KEYS],
        },
    )
    await session.commit()


async def main() -> None:
    check_only = "--check" in sys.argv
    async with AsyncSessionLocal() as session:
        if check_only:
            already_done = await check(session)
            print("OK: already migrated" if already_done else "NOT YET MIGRATED")
            return

        print("Before:")
        await check(session)
        print("\nMigrating...")
        await migrate(session)
        print("\nAfter:")
        done = await check(session)
        print("OK: migration verified" if done else "WARNING: verification failed after migrating")


if __name__ == "__main__":
    asyncio.run(main())

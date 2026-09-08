-- Remove the "science" and "official" (PIB) categories.
--
-- Context: both categories were permanently empty in the feed because of
-- the multi-source feed gate (>= 2 distinct sources required) combined with
-- too few seeded sources per category — PIB was the sole "official" source
-- (structurally can never reach 2), and "science" only had 2 sources total.
-- Decision: drop both as curated categories. Science is now reachable only
-- via a user's own custom topic search; PIB is dropped as not important.
-- App/backend code changes landed alongside this migration (seed_sources.py,
-- topic_filters.py, main.py DEFAULT_PREFERENCES).
--
-- Run this AFTER deploying the corresponding backend code, on both droplets'
-- shared Postgres instance. Wrapped in a transaction; review the SELECTs
-- before committing if you want to eyeball affected rows first.

BEGIN;

-- 1. Re-categorize the two ex-science sources into "tech" (their original
--    feed content), matching the seed_sources.py update.
UPDATE sources
SET category = 'tech'
WHERE slug IN ('indian-express-science', 'toi-science');

-- 2. Retire the PIB source. Deactivating (not deleting) preserves any
--    already-ingested articles/clusters attributed to it; switch to the
--    DELETE below instead if you want it gone outright.
UPDATE sources
SET status = 'disabled'
WHERE slug = 'pib';

-- Alternative — hard delete instead of disabling (only if you don't care
-- about keeping historical PIB articles/clusters attributable to a live
-- source row; check FK constraints from articles/clusters before using this):
-- DELETE FROM sources WHERE slug = 'pib';

-- 3. Strip "science"/"official" out of any user's saved preferences so a
--    stale key doesn't linger as a dead, unselectable tab. preferences is a
--    plain `json` column, so round-trip through jsonb for the array
--    manipulation and cast back.
UPDATE users
SET preferences = (
    jsonb_set(
        jsonb_set(
            preferences::jsonb,
            '{enabled_categories}',
            COALESCE(
                (
                    SELECT jsonb_agg(elem)
                    FROM jsonb_array_elements(preferences::jsonb -> 'enabled_categories') elem
                    WHERE elem::text NOT IN ('"science"', '"official"')
                ),
                '[]'::jsonb
            )
        ),
        '{custom_categories}',
        COALESCE(
            (
                SELECT jsonb_agg(elem)
                FROM jsonb_array_elements(preferences::jsonb -> 'custom_categories') elem
                WHERE elem::text NOT IN ('"science"', '"official"')
            ),
            '[]'::jsonb
        )
    )
)::json
WHERE preferences::jsonb -> 'enabled_categories' @> '["science"]'
   OR preferences::jsonb -> 'enabled_categories' @> '["official"]'
   OR preferences::jsonb -> 'custom_categories' @> '["science"]'
   OR preferences::jsonb -> 'custom_categories' @> '["official"]';

COMMIT;

-- Verification queries (run separately, no transaction needed):
--
-- SELECT slug, category, status FROM sources WHERE slug IN
--   ('pib', 'indian-express-science', 'toi-science');
--
-- SELECT count(*) FROM users
--   WHERE preferences::jsonb -> 'enabled_categories' @> '["science"]'
--      OR preferences::jsonb -> 'enabled_categories' @> '["official"]';
--   -- expect 0 after the migration

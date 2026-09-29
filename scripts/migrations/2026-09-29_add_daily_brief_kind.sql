-- Add daily_briefs.kind ('brief' | 'wrapup'), so a second same-day build (the
-- Late-Night Wrap-up, today's stories up to 7 PM IST) can live alongside the
-- morning Brief without colliding on brief_date. See app/models.py's
-- DailyBrief and app/services/daily_brief.py / daily_brief_select.py /
-- daily_brief_script.py.
--
-- Run this BEFORE deploying the corresponding backend code, on both
-- droplets' shared Postgres instance. Existing rows default to kind='brief',
-- which is what they already are.

BEGIN;

ALTER TABLE daily_briefs ADD COLUMN IF NOT EXISTS kind VARCHAR(16) NOT NULL DEFAULT 'brief';

-- brief_date was unique on its own (unique=True, index=True on the column);
-- replace that with a composite unique constraint on (brief_date, kind).
ALTER TABLE daily_briefs DROP CONSTRAINT IF EXISTS daily_briefs_brief_date_key;
DROP INDEX IF EXISTS ix_daily_briefs_brief_date;
CREATE UNIQUE INDEX IF NOT EXISTS ix_daily_briefs_brief_date_kind ON daily_briefs (brief_date, kind);

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'daily_briefs' AND column_name = 'kind';
-- \d daily_briefs   -- confirm the old unique constraint/index on brief_date
--                       alone is gone and ix_daily_briefs_brief_date_kind exists.

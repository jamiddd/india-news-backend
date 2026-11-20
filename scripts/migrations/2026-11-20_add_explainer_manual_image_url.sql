-- Add explainers.manual_image_url: an admin's manual pick from among an
-- explainer's attached source clusters' own article images (see
-- app/admin_explainers.py's image picker), overriding the auto-derived
-- hero_image_url wherever this explainer's image is shown. NULL means
-- "no override, keep the auto-derived hero" — see main.py's
-- list_explainers / search_explainers / get_explainer.
--
-- Run this BEFORE deploying the corresponding backend code (app/main.py /
-- app/models.py / app/admin_explainers.py), on both droplets' shared
-- Postgres instance. Existing rows are left NULL (no override — unchanged
-- auto-derived behavior). The backend also adds the column idempotently
-- at startup (app/main.py lifespan), so this is belt-and-braces.

BEGIN;

ALTER TABLE explainers ADD COLUMN IF NOT EXISTS manual_image_url TEXT;

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'explainers' AND column_name = 'manual_image_url';

-- Add breaking_stories.manual_image_url: an admin's manual pick from among
-- the story's own cluster's article images (see app/admin_breaking.py's
-- image picker), overriding the auto-selected image wherever this breaking
-- story's image is shown. NULL means "no override, keep auto-selecting" —
-- see main.py's list_breaking_stories / get_breaking_story.
--
-- Run this BEFORE deploying the corresponding backend code (app/main.py /
-- app/models.py / app/admin_breaking.py), on both droplets' shared Postgres
-- instance. Existing rows are left NULL (no override — unchanged
-- auto-selected behavior).

BEGIN;

ALTER TABLE breaking_stories ADD COLUMN IF NOT EXISTS manual_image_url TEXT;

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'breaking_stories' AND column_name = 'manual_image_url';

-- Add story_timeline_features.manual_image_url: an admin's manual pick from
-- among a timeline chain's own article images (see app/admin_timelines.py's
-- image picker), overriding the auto-selected image wherever this
-- timeline's image is shown. NULL means "no override, keep auto-selecting"
-- — see main.py's list_timeline_features / list_archived_timeline_features
-- / get_timeline_feature.
--
-- Run this BEFORE deploying the corresponding backend code (app/main.py /
-- app/models.py / app/admin_timelines.py), on both droplets' shared
-- Postgres instance. Existing rows are left NULL (no override — unchanged
-- auto-selected behavior).

BEGIN;

ALTER TABLE story_timeline_features ADD COLUMN IF NOT EXISTS manual_image_url TEXT;

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'story_timeline_features' AND column_name = 'manual_image_url';

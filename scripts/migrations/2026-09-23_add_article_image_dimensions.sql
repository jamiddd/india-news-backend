-- Add articles.image_width / articles.image_height: pixel dimensions of
-- image_url, read from the image's own header bytes at ingest time (see
-- image_extractor.fetch_image_dimensions). Lets the timeline feed rank
-- candidate images by quality (HD first) instead of just picking whichever
-- article happens to be first/most-recent — see main.py's
-- _cluster_to_list_out(image_priority_sort=True).
--
-- Run this BEFORE deploying the corresponding backend code (poller.py /
-- models.py / image_extractor.py), on both droplets' shared Postgres
-- instance. New/updated rows populate both columns once the code is live;
-- existing rows are left NULL (treated as "quality unknown", not "not HD" —
-- see backfill_timeline_image_dimensions.py for backfilling the small set
-- of articles that actually feed the active timeline chains).

BEGIN;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_width INTEGER;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_height INTEGER;

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'articles' AND column_name IN ('image_width', 'image_height');

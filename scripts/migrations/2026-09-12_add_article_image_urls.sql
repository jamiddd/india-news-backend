-- Add articles.image_urls: every distinct image found for an article
-- (RSS-declared image, then scraped og:image), deduped and in priority
-- order. image_url is unchanged and stays image_urls[0] going forward —
-- this is additive, existing readers of image_url need no changes.
--
-- Run this BEFORE deploying the corresponding backend code (poller.py /
-- models.py), on both droplets' shared Postgres instance. New/updated rows
-- populate image_urls once the code is live; existing rows are left NULL
-- (image_url on those rows is still correct on its own).

BEGIN;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_urls JSON;

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'articles' AND column_name = 'image_urls';

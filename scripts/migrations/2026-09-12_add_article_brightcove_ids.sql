-- Add articles.brightcove_account_id/player_id/video_id: captured whenever
-- a Brightcove embed is found on the article page, even if video_url ends
-- up NULL because the resolved manifest carried an expiring fastly_token
-- (see extractor.is_expiring_signed_video_url). Lets GET
-- /api/v1/articles/{id}/video-url re-resolve a fresh manifest on demand,
-- right before playback, instead of the story permanently falling back to
-- its image once the scrape-time token expires (observed live on Al
-- Jazeera stories).
--
-- Run this BEFORE deploying the corresponding backend code, on both
-- droplets' shared Postgres instance.

BEGIN;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS brightcove_account_id VARCHAR(32);
ALTER TABLE articles ADD COLUMN IF NOT EXISTS brightcove_player_id VARCHAR(64);
ALTER TABLE articles ADD COLUMN IF NOT EXISTS brightcove_video_id VARCHAR(32);

COMMIT;

-- Verification (run separately, no transaction needed):
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'articles' AND column_name LIKE 'brightcove_%';

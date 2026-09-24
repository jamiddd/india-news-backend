# Video link analysis — week of 2026-09-16 to 2026-09-22

One-off dry-run analysis of how many ingested articles carry a video link,
their orientation, and duration. Read-only queries against production
(Supabase), no backend changes. Day boundaries are IST (Asia/Kolkata)
00:00–23:59:59, converted to UTC for the `published_at` query, matching the
day-boundary convention used elsewhere in this backend (see the IST/
INDIA_TZ zoneinfo usage in `app/services/{crossword,daily_games,polls}.py`).

Scripts: `backend/scripts/dry_run_video_count_yesterday.py` and
`backend/scripts/dry_run_video_orientation.py` (both accept `--date` or a
`--start`/`--end` range).

## Video link coverage

| Date | Total articles | With video | % |
|---|---|---|---|
| Mon 2026-09-16 | 7,109 | 172 | 2.4% |
| Tue 2026-09-17 | 6,764 | 206 | 3.0% |
| Wed 2026-09-18 | 6,632 | 204 | 3.1% |
| Thu 2026-09-19 | 5,018 | 131 | 2.6% |
| Fri 2026-09-20 | 4,517 | 117 | 2.6% |
| Sat 2026-09-21 | 6,631 | 184 | 2.8% |
| Sun 2026-09-22 | 6,751 | 191 | 2.8% |
| **Total** | **43,422** | **1,205** | **2.8%** |

- Consistently 2.4–3.1% of articles carry a video link — no anomalous day.
- **Free Press Journal** alone accounts for ~68% of all video articles every
  single day (856 of 1,205 for the week), dwarfing every other source.
  Hindustan Times Videos and The Hindu Videos are the only other
  reliably-present video sources; everything else is sporadic (1–3/day).
- Thu 09-19 and Fri 09-20 have noticeably lower total article volume
  (~4,500–5,000 vs ~6,600–7,100 other days) — worth checking whether that's
  a source outage, but out of scope for this analysis.

## Orientation (vertical vs. landscape)

Only known for **YouTube** videos: `Article.video_is_short` is populated by
`_fetch_youtube_video_meta()` (`app/services/extractor.py`) and is NULL for
every non-YouTube video (Brightcove, direct-stream) per the column comment
in `app/models.py`. There is no width/height/aspect-ratio field for video
the way `image_width`/`image_height` exists for images, so the ~8% of
videos from non-YouTube sources are reported separately rather than
guessed at.

| Date | Video | Vertical | Landscape | Unknown | % vertical (of known) |
|---|---|---|---|---|---|
| 2026-09-16 | 172 | 48 | 109 | 15 | 31% |
| 2026-09-17 | 206 | 60 | 129 | 17 | 32% |
| 2026-09-18 | 204 | 55 | 135 | 14 | 29% |
| 2026-09-19 | 131 | 38 | 82 | 11 | 32% |
| 2026-09-20 | 117 | 28 | 77 | 12 | 27% |
| 2026-09-21 | 184 | 37 | 132 | 15 | 22% |
| 2026-09-22 | 191 | 66 | 111 | 14 | 37% |
| **Total** | **1,205** | **332** | **775** | **98** | **30.0%** |

Ratio vertical:landscape ≈ **3:7** (30.0% of orientation-known videos are
vertical), stable day to day (22–37%).

## Duration

`Article.video_duration_seconds` tracks orientation coverage exactly — it's
also YouTube-only, NULL for Brightcove/direct-stream. 333 of 1,205 videos
for the week have it set (the vertical+landscape YouTube set).

- Range: 3s – 455s (~7.6 min)
- Avg duration, vertical (Shorts): **~68s**
- Avg duration, landscape (regular): **~417s (~7 min)**

Expected pattern — Shorts capped near 3 min, regular YouTube videos running
much longer.

## Takeaways

- Video is a small slice of ingestion (~2.8% of articles), concentrated in
  a handful of dedicated video-feed sources (Free Press Journal, HT
  Videos, The Hindu Videos).
- Orientation/duration metadata only exists for YouTube; Brightcove and
  direct-stream videos (~8% of video links) carry neither today.

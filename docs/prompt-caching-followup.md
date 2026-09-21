# Prompt caching follow-up (deferred)

Prompted by an Anthropic Console notice (2026-09-10): direct API spend could
drop up to ~30% by caching repeated system prompts. Claude Code itself is
excluded (handles caching automatically) — this is about our own backend's
calls to the Anthropic API.

## Status

`backend/app/services/enrichment.py` already does this correctly —
`ENRICHMENT_SYSTEM_PROMPT` is sent with `cache_control: {"type": "ephemeral"}`.
This is the highest-volume call (runs per article/cluster), so most of the
available savings are already captured.

## Remaining candidates, in priority order

1. **`breaking_narrative.py`** — `SYSTEM_PROMPT` and
   `NARRATIVE_ONLY_SYSTEM_PROMPT` are large static blocks sent as plain
   strings through `call_claude()`. Runs on every fast-moving story
   evaluation/refresh — likely the second-highest-volume LLM call after
   enrichment. Clear next target: mirror enrichment.py's pattern (wrap the
   system string in a list, add `cache_control`).

2. **`timeline_narrative.py`** — `SYSTEM_PROMPT` is static, sent plain.
   Runs once per timeline "story so far" generation/refresh — lower volume
   (per-story, not per-article) but still a free win with the same
   three-line change.

3. **`polls.py`** — static `system` string, but only runs once a day.
   Caching has a minimum-token threshold and cache writes cost more than a
   normal call, so at this frequency it may not pay off. Low priority —
   revisit only if poll generation frequency increases.

4. **`llm_gen.py`** callers (`daily_games.py`, `editorial_features.py`,
   `apiverve_client.py`) — generic low-frequency, largely one-off generation
   jobs. Not worth chasing unless one of these turns out to be
   higher-volume than it currently looks.

## Next step when picked back up

Implement #1 (breaking_narrative.py) first, following the exact pattern
already proven in enrichment.py. #2 is a small follow-up after that. #3 and
#4 are not worth doing unless call volume changes.

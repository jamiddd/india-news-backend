"""Helps an admin draft an Explainer from a top story cluster: the admin
still picks the story and writes the question by hand (a story-context form
in admin_explainers.py, not a Claude call — see below for why), and this
module finds real background clusters already in OUR OWN database to
pre-attach as sources before generation.

This is the third sourcing lane alongside ad-hoc admin questions and
trending-search suggestions (see the Explainers feature plan/memory). The
flow is: admin browses top stories -> picks one -> writes a question
informed by seeing it -> this module's suggest_background_clusters() finds
background from our own DB -> admin_explainers.py creates the Explainer row
with everything pre-attached -> lands on the existing review page, where the
source picker still lets the admin add/remove/search before hitting
Generate, same review step every other explainer gets.

An earlier version of this module also had Claude phrase the question from
the headline automatically. Dropped 2026-09-28, per the user: having the
admin write it removes a Claude call that could produce an awkward question,
and — more importantly — the review page has no field to edit the question
after the fact, so a bad auto-phrased question had no fix short of
discarding the draft. Writing it up front sidesteps that gap entirely.

No web search, ever — decided 2026-09-28, final (see the plan's "Backend —
sourcing" section and the project-explainers-feature memory). Background
comes entirely from cluster_search.search_clusters(), the same
ILIKE-on-headline/summary query GET /search and the admin source picker
already use, so every suggested source is something already in our own
`sources`/`story_clusters` tables — never an outside fact.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, Source, StoryCluster
from app.services.cluster_search import search_clusters
from app.services.daily_brief_select import CORE_CATEGORIES, FALLBACK_CATEGORIES
from app.services.dedup import STOPWORDS

MAX_BACKGROUND_CLUSTERS = 6
MAX_CANDIDATE_PHRASES = 10
# Explainer.category is constrained to the admin dropdown's list (see
# admin_explainers.py CATEGORIES), which is CORE_CATEGORIES +
# FALLBACK_CATEGORIES, not Source.category's own free-er vocabulary
# (e.g. "general").
_CATEGORIES = CORE_CATEGORIES + FALLBACK_CATEGORIES

_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")


def _significant_words(text: str) -> list[str]:
    """Words as they actually appear in `text` — original casing and
    hyphenation kept, since these feed a literal ILIKE substring search —
    with stopwords and short filler dropped. Reuses app.services.dedup's
    STOPWORDS so "significant" means the same thing here as it does for
    clustering itself."""
    return [w for w in _WORD_RE.findall(text) if len(w) >= 4 and w.lower() not in STOPWORDS]


def _is_distinctive(word: str, *, is_first_word: bool) -> bool:
    """A word likely to recur verbatim across independently-written outlets'
    headlines about the same story: hyphenated (compound/technical terms
    like "third-language") or containing an uppercase letter (proper nouns
    and acronyms like "CBSE", "Bhandari") — UNLESS `word` is the first word
    of its text, where a capital letter proves nothing: English capitalises
    the first word of any sentence whether or not it's a proper noun.

    Confirmed as a real bug in production, 2026-09-28: the first published
    explainer's question was "Why did the protests erupt in Ujjain?", grounded
    in a cluster headlined "Protests erupt in Ujjain over partial demolition
    of Shahi Masjid...". "Protests" — capitalised only because it opened the
    headline — got tried before "Ujjain", "Shahi", or "Masjid" (the actual
    proper nouns, a few words later), and being an extremely common news
    word, its ILIKE search alone returned enough unrelated "protest" stories
    (a Congress protest in Jharkhand, an LPU campus story, ...) to fill the
    whole background-cluster budget before any real anchor term got a turn.
    A sentence-initial word still counts as distinctive if it's hyphenated or
    a true acronym (ALL-CAPS, e.g. a headline that happens to start with
    "CBSE") — only a merely-capitalised first letter in first position is
    untrustworthy."""
    if "-" in word:
        return True
    if is_first_word:
        return word.isupper()
    return any(ch.isupper() for ch in word)


def candidate_phrases(*texts: str, max_phrases: int = MAX_CANDIDATE_PHRASES) -> list[str]:
    """Search terms pulled from `texts` (typically a cluster's headline and
    summary) for finding background clusters, most likely to recur verbatim
    in an unrelated outlet's coverage first.

    Distinctive single words (see _is_distinctive) come first, IN ORDER OF
    APPEARANCE regardless of how deep into a sentence they sit — not after
    every multi-word phrase, and not sorted by n-gram length. That ordering
    is deliberate, not incidental: the CBSE/three-language test run
    (2026-09-28) found that the load-bearing term is often near the END of a
    summary sentence ("...under the newly introduced three-language
    scheme"), so an earlier draft of this function that tried all 4-word
    windows before any 2-word or single-word term buried that word past the
    phrase budget and never found it. A plain positional scan has the same
    problem no matter which n-gram size goes first, since the term's
    position in the sentence doesn't change — only skipping straight to
    "is this word distinctive" sidesteps it. The same case also had the
    headline itself spell it "third-language" while the summary said
    "three-language"; both are distinctive unigrams pulled from their
    respective texts, so both get tried.

    Multi-word n-grams (2, 3, then 4 words — shorter phrases are more
    likely to recur verbatim across differently-phrased headlines than long
    ones) fill any remaining budget, followed by any non-distinctive single
    words not yet included. Deduplicated, case preserved so each phrase can
    be used directly as an ILIKE term."""
    per_text_words = [_significant_words(t) for t in texts if t]
    phrases: list[str] = []

    for words in per_text_words:
        for i, w in enumerate(words):
            if _is_distinctive(w, is_first_word=(i == 0)) and w not in phrases:
                phrases.append(w)

    for n in (2, 3, 4):
        for words in per_text_words:
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i + n])
                if phrase not in phrases:
                    phrases.append(phrase)

    for words in per_text_words:
        for w in words:
            if w not in phrases:
                phrases.append(w)

    return phrases[:max_phrases]


async def suggest_background_clusters(
    db: AsyncSession, trigger: StoryCluster, *, question: Optional[str] = None,
    limit: int = MAX_BACKGROUND_CLUSTERS,
) -> list[StoryCluster]:
    """Up to `limit` past clusters from our own database that give background
    for `trigger`, oldest first (so a prompt built from them reads as a
    timeline). Tries candidate_phrases() — built from `trigger`'s headline
    and summary, plus the admin's own `question` text when given, since the
    admin may phrase the angle using words neither the headline nor summary
    used — from most to least specific, accumulating hits until `limit` is
    reached. Excludes `trigger` itself. Never touches the web."""
    found: dict[int, StoryCluster] = {}
    texts = [trigger.headline, trigger.summary or ""]
    if question:
        texts.append(question)
    for phrase in candidate_phrases(*texts):
        if len(found) >= limit:
            break
        hits = await search_clusters(db, phrase, limit=limit)
        for cluster in hits:
            if cluster.id == trigger.id or cluster.id in found:
                continue
            found[cluster.id] = cluster
            if len(found) >= limit:
                break
    return sorted(found.values(), key=lambda c: c.first_seen_at)


async def suggest_category(db: AsyncSession, trigger: StoryCluster) -> str:
    """The category holding the most distinct sources among `trigger`'s
    articles — the same majority-vote rule daily_brief_select.py's
    category_of() uses for the same purpose, recomputed here for one cluster
    rather than a whole day's batch. Falls back to the first CORE_CATEGORIES
    entry when no source has a category recognised by the admin dropdown."""
    rows = await db.execute(
        select(Source.category, func.count(func.distinct(Article.source_id)))
        .join(Article, Article.source_id == Source.id)
        .where(Article.cluster_id == trigger.id)
        .group_by(Source.category)
        .order_by(func.count(func.distinct(Article.source_id)).desc())
    )
    for category, _count in rows:
        if category in _CATEGORIES:
            return category
    return _CATEGORIES[0]

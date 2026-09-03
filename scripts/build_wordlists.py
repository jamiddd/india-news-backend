"""
Build the committed word lists that drive Spelling Bee, Word Ladder and
(if we add it) Wordle, from SCOWL.

Why lists and not generation: these puzzles are set-filtering problems, not
creative ones. Given 7 letters, the valid Spelling Bee word list is exactly
the dictionary words that fit — there is nothing for a model to invent, and
asking one to produce the list gets you words outside the letter set and a
missing pangram (measured: 12 of 73 words invalid on one sample, no pangram
on another). The same holds for Word Ladder, where _shortest_ladder_steps
already computes the true answer by BFS given a real word pool.

Source: SCOWL (Spell Checker Oriented Word Lists) by Kevin Atkinson.
Chosen because its licence permits commercial use of the lists *and of the
output created from them*, which is what the generated files below are. The
two obvious alternatives do not: google-10000-english is derived from an LDC
corpus and its own LICENSE.md advises against commercial use, and
dwyl/english-words ships no licence at all ("Copyright still belongs to
them"). SCOWL's terms are attribution-only — scripts/../licenses/SCOWL-Copyright
must ship with the app, which this script writes out.

SCOWL's "size" levels double as a commonness gradient, so one source covers
both lists we need:
  * answers  (<= ANSWER_LEVEL) -- what the app may pick. Must be words a
    general audience recognises.
  * validity (<= VALIDITY_LEVEL) -- what the app accepts as a guess. Bigger,
    so a player is not told a word they know is not a word.
Note this is a speller's notion of common ("a spellchecker accepts it"), not
a frequency one. If puzzles feel obscure, lower ANSWER_LEVEL to 20.

Proper nouns need no filtering: SCOWL keeps them in separate
*-proper-names.* and *-upper.* files, which this never reads.

Usage:
    python3 scripts/build_wordlists.py                  # download, build, write
    python3 scripts/build_wordlists.py --scowl-dir DIR  # use an extracted copy
    python3 scripts/build_wordlists.py --dry-run        # report only, write nothing
"""
from __future__ import annotations

import argparse
import hashlib
import random
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "data" / "wordlists"
LICENSE_OUT = REPO_ROOT / "licenses" / "SCOWL-Copyright"

SCOWL_VERSION = "2020.12.07"
SCOWL_URL = (
    "https://downloads.sourceforge.net/project/wordlist/SCOWL/"
    f"{SCOWL_VERSION}/scowl-{SCOWL_VERSION}.tar.gz"
)
SCOWL_SHA256 = "5587667caa20c4891390c2d42dbb4d5c4c3f41bee77af1457ece3ba23fb859cc"

LEVELS = [10, 20, 35, 40, 50, 55, 60, 70, 80, 95]
ANSWER_LEVEL = 35
VALIDITY_LEVEL = 70

# Bee puzzles must land in a size band: too few words is unplayable, too many
# is a slog. Mirrors the 8-20 that _validate_bee enforces, with headroom.
BEE_MIN_WORDS, BEE_MAX_WORDS = 8, 40
LADDER_MIN_STEPS, LADDER_MAX_STEPS = 3, 6
LADDER_MAX_PAIRS = 4000

# SCOWL is a spelling dictionary, so its larger levels contain slurs and
# crude terms. This is a starting point, NOT a complete filter — the
# generated files are small enough to review, and should be reviewed before
# they ship. Kept here rather than pulled from a third-party blocklist so
# there is no second licence to clear.
BLOCKED_SUBSTRINGS = {
    "nigg", "fagg", "chink", "spick", "wetback", "kike", "raghead",
    "cunt", "fuck", "shit", "wank", "twat", "slut", "whore", "rape",
    "molest", "incest", "nazi", "jihad", "lynch", "suicide",
}


def _blocked(word: str) -> bool:
    return any(bad in word for bad in BLOCKED_SUBSTRINGS)


def fetch_scowl(dest: Path) -> Path:
    """Download and verify the pinned SCOWL release, returning its final/ dir."""
    archive = dest / f"scowl-{SCOWL_VERSION}.tar.gz"
    print(f"Downloading {SCOWL_URL}")
    urllib.request.urlretrieve(SCOWL_URL, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != SCOWL_SHA256:
        raise SystemExit(f"SCOWL checksum mismatch:\n  expected {SCOWL_SHA256}\n  got      {digest}")
    print(f"  checksum OK ({archive.stat().st_size / 1024:.0f} KB)")
    with tarfile.open(archive) as tar:
        tar.extractall(dest)
    return dest / f"scowl-{SCOWL_VERSION}"


def load_level(final_dir: Path, max_level: int) -> set[str]:
    """Every plain lowercase word at or below `max_level`.

    Only english-words.* and american-words.* are read: the proper-names and
    upper files are where SCOWL keeps names and acronyms, and neither belongs
    in a word game.
    """
    words: set[str] = set()
    for level in LEVELS:
        if level > max_level:
            break
        for stem in ("english-words", "american-words"):
            path = final_dir / "final" / f"{stem}.{level}"
            if not path.exists():
                continue
            for line in path.read_text(encoding="latin-1").splitlines():
                word = line.strip()
                if word.isascii() and word.isalpha() and word.islower() and not _blocked(word):
                    words.add(word)
    return words


def _mask(word: str) -> int:
    bits = 0
    for char in word:
        bits |= 1 << (ord(char) - 97)
    return bits


def _submasks(mask: int):
    """Every subset of `mask`, including itself. 7 letters -> 128 subsets,
    which is what makes the Bee scan tractable: without it, every candidate
    pangram would be a full pass over ~38k words."""
    sub = mask
    while True:
        yield sub
        if sub == 0:
            return
        sub = (sub - 1) & mask


def _spread(rows: list) -> list:
    """Deterministically shuffle generated puzzle rows.

    The natural build order is alphabetical, which would hand consecutive
    days near-identical puzzles (ABDICATE then ABDICATED) to any consumer
    that walks the file by date ordinal. Shuffling here makes the obvious
    `puzzles[ordinal % len(puzzles)]` correct by default rather than a
    latent bug. The seed is fixed, so a rebuild of unchanged input produces
    an identical file.
    """
    shuffled = list(rows)
    random.Random(20260903).shuffle(shuffled)
    return shuffled


def build_bee(answers: set[str]) -> list[tuple[str, str, int]]:
    """Precompute every playable (pangram, centre) pair.

    Deriving the letters *from* a pangram is what guarantees a pangram
    exists — the constraint the LLM path kept failing. Doing it here rather
    than at runtime means an unplayable puzzle can never reach a user.
    """
    by_mask: dict[int, list[str]] = {}
    for word in answers:
        if len(word) < 4:
            continue
        mask = _mask(word)
        if bin(mask).count("1") <= 7:
            by_mask.setdefault(mask, []).append(word)

    # Keyed by letter set, not by pangram: ARCHING, CHAGRIN, CHARGING and
    # RANCHING are one puzzle, not four, and without this the same seven
    # letters would come round several times in a year.
    puzzles: list[tuple[str, str, int]] = []
    seen_letters: set[frozenset[str]] = set()
    candidates = sorted(w for w in answers if len(set(w)) == 7 and 7 <= len(w) <= 9)
    for pangram in candidates:
        if frozenset(pangram) in seen_letters:
            continue
        pangram_mask = _mask(pangram)
        counts = {letter: 0 for letter in set(pangram)}
        for sub in _submasks(pangram_mask):
            words = by_mask.get(sub)
            if not words:
                continue
            for letter in counts:
                if sub & (1 << (ord(letter) - 97)):
                    counts[letter] += len(words)
        for letter in sorted(counts):
            if BEE_MIN_WORDS <= counts[letter] <= BEE_MAX_WORDS:
                puzzles.append((pangram, letter, counts[letter]))
                seen_letters.add(frozenset(pangram))
                break
    return _spread(puzzles)


def build_ladder(answers: set[str]) -> tuple[list[str], list[tuple[str, str, int]]]:
    """Four-letter pool restricted to the largest connected component, plus
    precomputed start/target pairs in the playable step band."""
    four = sorted(w for w in answers if len(w) == 4)
    buckets: dict[str, list[str]] = {}
    for word in four:
        for i in range(4):
            buckets.setdefault(word[:i] + "_" + word[i + 1:], []).append(word)

    def neighbours(word: str) -> set[str]:
        return {
            other
            for i in range(4)
            for other in buckets.get(word[:i] + "_" + word[i + 1:], [])
            if other != word
        }

    seen: set[str] = set()
    components: list[list[str]] = []
    for word in four:
        if word in seen:
            continue
        queue, seen_here = deque([word]), [word]
        seen.add(word)
        while queue:
            current = queue.popleft()
            for other in neighbours(current):
                if other not in seen:
                    seen.add(other)
                    seen_here.append(other)
                    queue.append(other)
        components.append(seen_here)
    components.sort(key=len, reverse=True)
    pool = sorted(components[0])
    pool_set = set(pool)

    # Every word gets a turn as a start word, capped per source. Taking pairs
    # greedily instead would fill the whole quota from the first few BFS runs,
    # so the same handful of start words would front every puzzle for years.
    pairs: list[tuple[str, str, int]] = []
    per_source = max(1, LADDER_MAX_PAIRS // len(pool) + 1)
    for start in pool:
        distances = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for other in neighbours(current):
                if other in pool_set and other not in distances:
                    distances[other] = distances[current] + 1
                    queue.append(other)
        taken = 0
        for target, steps in sorted(distances.items()):
            if LADDER_MIN_STEPS <= steps <= LADDER_MAX_STEPS and start < target:
                pairs.append((start, target, steps))
                taken += 1
                if taken >= per_source:
                    break
    return pool, _spread(sorted(pairs))[:LADDER_MAX_PAIRS]


def write_list(path: Path, lines: list[str], description: str, dry_run: bool) -> None:
    body = "\n".join(
        [
            f"# {description}",
            f"# Generated by scripts/build_wordlists.py from SCOWL {SCOWL_VERSION}. Do not edit by hand.",
            f"# SCOWL is (c) Kevin Atkinson and others; see licenses/SCOWL-Copyright.",
            f"# {len(lines)} entries",
        ]
        + lines
    ) + "\n"
    size = len(body.encode()) / 1024
    print(f"  {str(path.relative_to(REPO_ROOT)):46} {len(lines):>6} entries  {size:>6.0f} KB")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scowl-dir", type=Path, help="an already-extracted scowl-<version> directory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tmp = None
    try:
        if args.scowl_dir:
            final_dir = args.scowl_dir
        else:
            tmp = Path(tempfile.mkdtemp(prefix="scowl-"))
            final_dir = fetch_scowl(tmp)

        answers = load_level(final_dir, ANSWER_LEVEL)
        validity = load_level(final_dir, VALIDITY_LEVEL)
        print(f"\nSCOWL <= {ANSWER_LEVEL}: {len(answers)} answer words")
        print(f"SCOWL <= {VALIDITY_LEVEL}: {len(validity)} validity words")

        print("\nSpelling Bee")
        bee = build_bee(answers)
        print(f"  {len(bee)} playable (pangram, centre) pairs -> {len(bee) / 365:.1f} years of daily puzzles")

        print("\nWord Ladder")
        pool, pairs = build_ladder(answers)
        print(f"  pool {len(pool)} four-letter words (largest connected component)")
        print(f"  {len(pairs)} start/target pairs at {LADDER_MIN_STEPS}-{LADDER_MAX_STEPS} steps")

        print("\nWriting" + (" (dry run)" if args.dry_run else ""))
        write_list(OUT_DIR / "bee_answers.txt", sorted(w for w in answers if len(w) >= 4),
                   "Spelling Bee: words the puzzle may contain", args.dry_run)
        write_list(OUT_DIR / "bee_puzzles.txt", [f"{p} {c} {n}" for p, c, n in bee],
                   "Spelling Bee: pangram, centre letter, valid-word count", args.dry_run)
        write_list(OUT_DIR / "ladder_words.txt", pool,
                   "Word Ladder: four-letter pool, largest connected component", args.dry_run)
        write_list(OUT_DIR / "ladder_puzzles.txt", [f"{a} {b} {n}" for a, b, n in pairs],
                   "Word Ladder: start, target, true shortest-path steps", args.dry_run)
        write_list(OUT_DIR / "wordle_answers.txt", sorted(w for w in answers if len(w) == 5),
                   "Wordle: five-letter answer pool", args.dry_run)
        write_list(OUT_DIR / "wordle_guesses.txt", sorted(w for w in validity if len(w) == 5),
                   "Wordle: five-letter words accepted as guesses", args.dry_run)

        copyright_src = final_dir / "Copyright"
        if copyright_src.exists() and not args.dry_run:
            LICENSE_OUT.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(copyright_src, LICENSE_OUT)
            print(f"  {LICENSE_OUT.relative_to(REPO_ROOT)} (attribution, required)")
        return 0
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

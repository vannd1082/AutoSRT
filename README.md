# smart_srt — Sentence-aware TextGrid → SRT converter

Converts a voiced script (`script.txt`) + its forced-alignment TextGrid
(`input.TextGrid`, e.g. from MFA) into a subtitle file (`output.srt`),
using sentence structure and real speech pauses to decide where to
break subtitle lines — instead of a fixed word-count rule.

**Current version: v4.1** (`smart_srt_v4_1.py`)

This file is meant to be read on its own. If you're a developer or an
AI agent picking up this tool, you should not need to open the source
to understand what it does, why it's built this way, or how to change
it safely.

---

## 1. What problem this solves

Naive SRT generators cut subtitles every N words, ignoring where the
speaker actually paused and ignoring sentence boundaries. That produces
subtitles that split mid-phrase, or glue two unrelated sentences
together for a beat.

This tool instead:
1. Splits the **script** into sentences first (`. ! ?`, with guards —
   see §4).
2. Never lets a subtitle block cross a sentence boundary.
3. Within a sentence, only splits at points where the audio actually
   has a pause (comma, colon, closing quote, or any natural gap) —
   never at an arbitrary word count.
4. Falls back to accepting a weaker pause only when a block gets
   unreasonably long (see §4, point 7) — it does not silently glue
   long sentences together forever.

## 2. Usage

```bash
python smart_srt_v4_1.py script.txt input.TextGrid output.srt
```

- `script.txt` — the original voiced script, plain text, normal
  punctuation and capitalization.
- `input.TextGrid` — a Praat-format TextGrid with a tier named
  `"words"`, produced by a forced aligner (e.g. Montreal Forced
  Aligner / MFA) run against `script.txt` and its audio.
- `output.srt` — the file this script writes.

Requires the `praatio` Python package (`pip install praatio`).

Console output on every run:
- An **alignment self-check** (see §5).
- A **reading-speed warning** for any block that's too fast to read
  (see §6).
- A final line: `Done. N blocks written to output.srt`.

Neither console report changes the `.srt` file — they are diagnostics
for you to review, not automatic edits.

## 3. Design principles (read this before changing tunables)

These are the ground rules the whole algorithm is built on. Any change
to the tunables in §7 should respect these, or you'll reintroduce bugs
this tool was specifically built to avoid.

- **Sentence boundaries come from the script, not from the audio.**
  A pause in the audio never creates or removes a sentence boundary.
  Only `. ! ?` in `script.txt` do (with the guards in §4).
- **Splitting inside a sentence is pause-driven, not word-count-driven.**
  `MAX_WORDS` and `CHAR_LIMIT` are triggers that say "this block should
  *look* for a split point" — they never force a cut by themselves.
  Only `CHAR_LIMIT_HARD` (added in v4.1) can force a cut, and only as a
  last resort, at a much higher bar. See §4 point 7.
- **A bad cut is worse than a long line**, below `CHAR_LIMIT_HARD`. If
  no pause/punctuation point clears its bar, the block is left as one
  block, however long. This was an explicit decision, not an oversight
  — see changelog v3 rationale in §9.
- **Timing always comes from the actual audio.** The tool never
  stretches or shrinks display time to "fix" a fast-reading block; it
  only flags it (§6). Padding (`TAIL_PAD`) only extends a block's
  display end, and only up to where the next block starts.
- **Diagnostics over silent failure.** Alignment mismatches and
  reading-speed problems are printed, not silently absorbed. See §5, §6.

## 4. How splitting actually works, step by step

### 4.1 Sentence splitting
`script.txt` is tokenized on whitespace. A token ends a sentence if it
ends in `. ! ?`, **except**:
- It's an ellipsis (`...` or `…`) — e.g. `"and then... he understood"`
  is NOT split at `then...`.
- It's a known abbreviation — see the `ABBREVIATIONS` set in §7 (`Mr.`,
  `Dr.`, `e.g.`, `U.S.`, `a.m.`, etc.). Not exhaustive — add to the set
  if your script uses others (e.g. `Ven.` for "Venerable").
- It's a decimal number like `3.5` (the `.` inside a number is not a
  sentence end; `3.5!` or `3.5?` still would be, since those aren't
  ambiguous).

### 4.2 Word alignment (script ↔ TextGrid)
Every TextGrid word is matched to the corresponding script token, in
order, so we know each word's real start/end time. This step has two
non-obvious behaviors, both added specifically to fix real failures:

- **Hyphenated compounds**: if the current script token contains a
  hyphen (e.g. `half-present.`) and the forced aligner produced it as
  two separate words (`half`, `present`), the tool detects this and
  re-merges them into one aligned unit, keeping the script's original
  casing/punctuation and the combined start/end time. Without this,
  one hyphenated word could derail alignment for the entire rest of
  the file (see §9, v4 fix — this was a real bug that dropped match
  rate to 38% on a 2747-word script).
- **Windowed matching + resync**: matching only looks a small distance
  ahead (12 script tokens / 6 TextGrid words) instead of scanning to
  the end of the whole script on every miss. This avoids two problems:
  it's faster, and it avoids accidentally matching a word to the wrong,
  much-later occurrence of the same text (common with repeated phrases
  like "you are... you are...").
- Words that still can't be matched keep the TextGrid's own text and
  timing, but are marked as unmatched (`script_idx = None`) and don't
  advance the sentence position — this is what shows up in the
  alignment self-check (§5).

### 4.3 Splitting within a sentence — candidate priority
For each sentence, `find_best_split()` looks for a single best point to
cut, using this priority:

1. **Punctuation candidates**, pooled together and ranked purely by
   pause length (not by type — a colon can win over a comma if its
   pause is longer):
   - Comma (`,`) — counts if the pause after it is ≥ `COMMA_PAUSE` (0.20s).
   - Colon (`:`) or a closing quote (`"` `”` `'` `’`) — counts if the
     pause after it is ≥ `SOFT_PUNCT_PAUSE` (0.25s). *(Added in v4.1.)*
   - Whichever candidate has the **longest actual pause** wins — not
     the first one, not the geometrically central one.
2. If no punctuation candidate qualifies: the single longest pause
   anywhere in the sentence, if it's ≥ `PAUSE_THRESHOLD` (0.50s).
3. If neither of the above found anything, **and** the block is over
   `CHAR_LIMIT_HARD` (55 chars): tier-2 fallback — accept the weakest
   available pause anywhere, down to `FALLBACK_MIN_PAUSE` (0.10s), as a
   last resort. *(Added in v4.1 — see §9.)*
4. If nothing qualifies even at that bar, the block is left as one
   long line (below `CHAR_LIMIT_HARD`) or as the best it can do (above
   it) — never a blind mid-word cut.

Once a split point is found, the tool recurses on both halves, so a
long sentence can be broken into more than 2 blocks if needed.

### 4.4 Tail padding
Every block's display end time is extended by `TAIL_PAD` (1.2s), but
never past `GAP_BEFORE_NEXT` (0.05s) before the next block starts. This
gives the viewer more time to finish reading without overlapping the
next subtitle.

## 5. Alignment self-check (read this if match rate looks low)

Printed after every run:

```
--- Alignment self-check ---
  Script tokens total:   2747
  TextGrid words total:  2752
  Matched to script:     2747 (99.8%)
  Hyphenated words re-merged: 5 (...)
-----------------------------
```

- **Match rate below ~98%** triggers a `WARNING` line. This means some
  words in the `.srt` may have inherited timestamps from the wrong
  script position — spot-check the output against the audio.
- **A low match rate is a symptom, not the disease.** In the one real
  case investigated so far, a 38% match rate was caused by ONE
  hyphenated word the aligner split into two, which then derailed
  matching for everything after it (fixed in v4 — see §9). If you see
  a similarly low rate again, the fix that worked was: find the exact
  TextGrid interval + script line where matching first goes wrong
  (usually near the start of the divergence, not where the numbers
  look worst), and check for a similar "one script token = N aligner
  words" case. Common culprits: hyphenated compounds, numbers written
  differently in speech vs. script (e.g. "twenty twenty-four" vs.
  "2024"), or contractions the aligner split differently.
- `skipped_script_tokens` counts how many script tokens were jumped
  over during matching — non-zero doesn't necessarily mean a problem
  (a few is normal), but a large number alongside a low match rate
  means the same investigation applies.

## 6. Reading-speed check

Printed after every run, if any block exceeds `MAX_READING_CPS`
(20 characters/second):

```
--- Reading speed check: 1 block(s) above 20 chars/sec ---
  Block 6: 22.9 cps (48 chars / 2.10s) -- "What if the problem was..."
  (These are flagged for review only; timing was not changed,
   since it must stay matched to the actual audio.)
-----------------------------
```

This is advisory only. A short block that's technically "too fast" by
the cps formula (e.g. 4 words in 0.8s) is often still readable in
practice — use judgment, don't treat every flagged line as broken.

## 7. Tunables reference

All at the top of the file. Safe to adjust without touching any logic.

| Name | Default | Meaning |
|---|---|---|
| `PAUSE_THRESHOLD` | `0.50` | Min pause (s) to count as a break point when there's no punctuation involved. |
| `COMMA_PAUSE` | `0.20` | Min pause (s) after a comma to count as a break candidate. |
| `SOFT_PUNCT_PAUSE` | `0.25` | Min pause (s) after `:` or a closing quote to count as a break candidate. *(v4.1)* |
| `MAX_WORDS` | `20` | Soft trigger: blocks longer than this look for a split point (does not force one). |
| `CHAR_LIMIT` | `42` | Soft trigger: blocks longer than this (chars) look for a split point (does not force one). Common subtitle norm. |
| `CHAR_LIMIT_HARD` | `55` | Hard-er trigger *(v4.1)*: blocks longer than this, with no normal split point found, fall back to tier-2 splitting (weak-pause acceptance). |
| `FALLBACK_MIN_PAUSE` | `0.10` | Tier-2 last-resort minimum pause (s) accepted as a break point. *(v4.1)* |
| `GAP_BEFORE_NEXT` | `0.05` | Seconds of breathing room kept before the next block's start when padding. |
| `TAIL_PAD` | `1.2` | Seconds added to a block's display end (capped by `GAP_BEFORE_NEXT` before the next block). |
| `MAX_READING_CPS` | `20` | Characters/second above which a block is flagged (not changed) as too fast to read. |
| `ABBREVIATIONS` | (set, see source) | Tokens ending in `.` that are NOT sentence ends. Add to this set as needed. |

**Tuning guidance:**
- If subtitles are cutting too aggressively at weak pauses → raise
  `SOFT_PUNCT_PAUSE` and/or `COMMA_PAUSE`.
- If long sentences are still gluing into oversized blocks → lower
  `CHAR_LIMIT_HARD` (this is what widens tier-2's reach — raising
  `FALLBACK_MIN_PAUSE` would make tier-2 stricter, the opposite of
  what you want here).
- If a script uses an abbreviation not in the list and a sentence gets
  cut mid-abbreviation → add it to `ABBREVIATIONS`.

## 8. Known limitations

- **`ABBREVIATIONS` is a fixed list, not exhaustive.** Uncommon
  abbreviations will still be misread as sentence ends.
- **Ellipsis and decimal guards are pattern-based**, not a full
  grammar parser — extremely unusual punctuation combos may not be
  handled.
- **No semantic awareness.** Splitting is 100% pause/punctuation
  driven. A grammatically "ideal" split point with no corresponding
  audio pause will not be used — this is intentional (see §3), not a
  gap to fill with NLP. A semantic/NLP-based approach was considered
  and explicitly rejected: it risks producing subtitles that are
  grammatically clean but rhythmically desynced from the spoken audio.
- **`CHAR_LIMIT` alone (42) is a soft signal only** — a block can
  still exceed it if no pause/punctuation split is available and it
  hasn't yet crossed `CHAR_LIMIT_HARD` (55). This gap (42–55 chars) is
  deliberate headroom, not a bug.
- **Matching window is bounded** (12 script tokens / 6 TextGrid words
  lookahead). Extremely large local divergences between script and
  audio (many consecutive unmatched words) could still cause a
  temporary misalignment. Check the alignment self-check (§5) if in
  doubt.

## 9. Changelog

### v4.1 (current)
- **Added:** colon (`:`) and closing quotes (`"` `”` `'` `’`) as valid
  split candidates, pooled with commas and ranked by actual pause
  length via `SOFT_PUNCT_PAUSE` (0.25s bar). Fixes sentences like:
  > `insisting: "This shouldn't be happening..." the roof keeps leaking...`

  which previously stayed as one 125-character block because neither
  the colon nor the closing quote/ellipsis was ever treated as a break
  point.
- **Added:** two-tier splitting. Below `CHAR_LIMIT_HARD` (55 chars),
  behavior is unchanged from v4 — no split point found means the block
  is left as-is. Above that bar, if still no split point clears the
  normal bars, tier-2 fallback accepts the weakest available pause
  (down to `FALLBACK_MIN_PAUSE` = 0.10s) so an oversized, pause-poor
  sentence still gets broken up instead of staying as one long block.
- Verified: all v4 test cases (abbreviations, decimals, ellipsis,
  alignment self-check, hyphen re-merging) re-run with byte-identical
  `.srt` output after this change — nothing in v4's behavior regressed.

### v4
- **Added:** abbreviation/decimal guard so `Mr.`, `U.S.`, `e.g.`,
  `3.5`, etc. are not mistaken for sentence endings.
- **Added:** alignment self-check report (match rate, skipped tokens).
- **Added:** `CHAR_LIMIT` (42 chars) as a second soft trigger alongside
  `MAX_WORDS`.
- **Added:** reading-speed warning (`MAX_READING_CPS`), flag-only.
- **Added:** ellipsis (`...`) handling — no longer 3 fake sentence
  ends, no longer a false comma-like break point.
- **Fixed (bug found via real-world testing, not a numbered feature):**
  a 2747-word real script produced only 38.2% alignment match rate.
  Root cause: the aligner (MFA) split one hyphenated compound word
  ("half-present.") into two separate audio words ("half", "present").
  The old matching logic scanned forward to the end of the entire
  script looking for a literal match, found none, and got stuck at
  that position — every word after it matched wrong or fell back to
  losing its original casing/punctuation. Fixed by (a) detecting
  hyphenated script tokens and re-merging split aligner words back
  into one unit, and (b) replacing "scan to end of script" with a
  small bounded lookahead window plus a resync check, so one bad match
  can't derail everything downstream. Also fixed in the same pass: an
  `fmt()` timestamp rounding bug that could output an invalid
  `SS,1000` millisecond field instead of carrying over to the next
  second.
- Verified: v3 test cases re-run with identical `.srt` output after
  this fix.

### v3
- **Rewrite** of the original word-count-based script into a
  sentence-first design:
  - Sentences are split from the script FIRST (`. ! ?`), and a
    subtitle block never crosses that boundary.
  - Removed `LEADING_CONNECTORS` / `TRAILING_CLAUSE_STARTERS` entirely
    (the old "pull word X into the previous/next block" system).
  - Splitting inside a sentence is pause-driven: when a sentence has
    multiple comma candidates, the one with the **longest actual
    pause** wins — not the first comma, not the one closest to the
    middle.
  - `MAX_WORDS` became a soft signal only: if a sentence has no comma
    and no pause above threshold, it is **not** force-cut — left as
    one block, on the principle that a bad cut is worse than a long
    line. (This is the origin of the "no bad cuts" rule referenced
    throughout this README — it was an explicit tradeoff Van chose,
    later refined by tier-2 fallback in v4.1 for extreme cases.)

### v2 (original / prior tool, pre-dates this documented history)
- Word-count + pause based splitting, with a `LEADING_CONNECTORS` /
  `TRAILING_CLAUSE_STARTERS` system to pull certain words across block
  boundaries, and a merge step for short trailing blocks. Superseded
  entirely by the v3 rewrite above.

---

*This README should be kept in sync with the source whenever tunables,
split logic, or matching behavior change. If you change a value in §7
or add a new rule to §4, add a corresponding line to §9 so the next
reader (human or AI) doesn't have to diff the source to find out what
changed and why.*
"# AutoSRT" 

"""
smart_srt_v4_1.py
Sentence-aware TextGrid -> SRT converter

v4.1 changes (on top of v4):
  6. Colon (":") and closing quotes ('"' / '”' / "'" / "’") are now
     valid split points too, on the same footing as a comma, using a
     dedicated pause bar (SOFT_PUNCT_PAUSE) between the comma bar and
     the plain-pause bar. This fixes long sentences like:
       'insisting: "This shouldn't be happening..." the roof keeps...'
     which previously stayed glued into one oversized block because
     neither the colon nor the quote was ever treated as a break point.
  7. Two-tier splitting for long blocks: below CHAR_LIMIT_HARD (55
     chars), the original "no bad cuts" principle is untouched -- if no
     pause/punct point clears its bar, the block is left as one long
     line. Above CHAR_LIMIT_HARD, a block that still found no usable
     split point falls back to accepting the weakest available pause
     (down to FALLBACK_MIN_PAUSE = 0.10s) as a last resort, so a very
     long, pause-poor sentence doesn't stay as one oversized subtitle.

Everything from v4 (see below) is unchanged in behavior for blocks that
don't hit the new colon/quote or CHAR_LIMIT_HARD paths -- verified by
re-running all prior test cases with identical output.

v4 changes (on top of v3), kept for reference:
  1. Abbreviation / decimal number guard, so "Mr.", "U.S.", "e.g.",
     "3.5" etc. are not mistaken for sentence endings.
  2. Alignment self-check: prints a match-rate summary after running,
     so a silent misalignment (TTS skipped/added a word) shows up as a
     number you can see, instead of hiding inside the .srt.
  3. Character-per-line limit (CHAR_LIMIT), on top of the existing
     word-count soft cap, so a block isn't "short in words" but still
     visually too wide to read.
  4. Reading-speed warning: flags any block whose char/sec rate is too
     fast for a viewer to read comfortably. Flags only — does not
     auto-edit the timing, since the audio timing is ground truth.
  5. Ellipsis ("...") handling: "..." no longer counts as 3 sentence
     endings, and is also not treated as a normal comma-like pause
     point by accident.
  Also fixed in v4 (bugfix, not a numbered feature): align_words() now
  handles hyphenated compounds that a forced aligner (MFA) splits into
  separate words (e.g. script "half-present." vs aligner "half" +
  "present"), and matching is windowed + resync-capable instead of
  scanning to the end of the script on every miss -- this was the fix
  for a real case where match rate was 38% due to one such compound
  word derailing everything after it.

Nothing about the core split logic changed since v3: pause-first,
punctuation candidates ranked by pause length win among themselves,
MAX_WORDS/CHAR_LIMIT never force-cut on their own (only CHAR_LIMIT_HARD
triggers tier-2 fallback, as of v4.1).

See README.md for the full feature list, tunables reference, and
troubleshooting guide -- it documents behavior at a level meant to be
readable without going through this source file.

Usage:
    python smart_srt_v4_1.py script.txt input.TextGrid output.srt
"""

import re
import sys
from praatio import textgrid

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
PAUSE_THRESHOLD = 0.50       # pause (s) inside a sentence that forces a break
COMMA_PAUSE = 0.20           # min pause (s) after a comma to treat it as a break candidate
SOFT_PUNCT_PAUSE = 0.25      # min pause (s) after ":" or a closing quote to count as a break
MAX_WORDS = 20               # soft cap; only used if no pause/comma break exists
CHAR_LIMIT = 42              # soft cap on characters per block (common subtitle norm ~40-42)
CHAR_LIMIT_HARD = 55         # if a block exceeds THIS, tier-2 kicks in: lower the pause bar
FALLBACK_MIN_PAUSE = 0.10    # tier-2 minimum pause (s) accepted as a last-resort break point
GAP_BEFORE_NEXT = 0.05       # seconds of breathing room before the next block starts
TAIL_PAD = 1.2               # seconds added to a block's display end (capped by next block)
MAX_READING_CPS = 20         # characters/sec above this = flagged as "too fast to read"
END_PUNCT_CHARS = ".!?"

# Common abbreviations that end in "." but do NOT end a sentence.
# Matched case-sensitively against the token stripped of trailing punctuation
# other than the final period, e.g. "Mr." -> checked as "Mr."
ABBREVIATIONS = {
    "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "St.",
    "vs.", "etc.", "e.g.", "i.e.", "U.S.", "U.K.", "a.m.", "p.m.",
    "Fig.", "No.", "Vol.", "Rev.", "Gen.", "Col.", "Ave.",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt(t):
    # Round to the nearest millisecond FIRST, then derive h/m/s/ms from that
    # total, so rounding (e.g. 5.9996 -> 6.000) can never spill into an
    # invalid "1000ms" field -- it carries over into seconds instead.
    total_ms = int(round(t * 1000))
    h, rem_ms = divmod(total_ms, 3600 * 1000)
    m, rem_ms = divmod(rem_ms, 60 * 1000)
    s, ms = divmod(rem_ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def norm(w):
    """Lowercase, strip punctuation, for matching TextGrid words to script words."""
    return re.sub(r"[^A-Za-z0-9']+", "", w).lower()


def tokenize_script(text):
    """Split the script into raw tokens, keeping original punctuation/casing."""
    out = []
    for tok in re.findall(r"\S+", text):
        n = norm(tok)
        if n:
            out.append((n, tok))
    return out


def is_decimal_number(token):
    """True for tokens like '3.5', '0.20', '12.75%'."""
    return bool(re.match(r"^\d+\.\d+", token))


def ends_sentence(token):
    """
    Decide whether this script token is the END of a sentence.

    Guards added in v4:
      - Ellipsis "..." (or "…") is NOT a sentence end by itself.
      - Known abbreviations ("Mr.", "e.g.", "U.S.", ...) are not a
        sentence end.
      - Decimal numbers ("3.5") are not split on the internal period
        (tokenize_script already keeps them as one token, so this only
        matters if a token legitimately ends like "...3.5." — rare,
        but guarded anyway).
    """
    stripped = token.rstrip()
    if not stripped:
        return False

    if stripped.endswith("...") or stripped.endswith("…"):
        return False

    if stripped in ABBREVIATIONS:
        return False

    if is_decimal_number(stripped) and stripped[-1] not in "!?":
        # e.g. "3.5" alone is not a sentence end. "3.5!" or "3.5?" still is,
        # handled below by falling through to the normal check.
        if stripped[-1] == ".":
            return False

    last_char = stripped[-1]
    return last_char in END_PUNCT_CHARS


def split_sentences(script_tokens):
    """
    Group script tokens into sentences using . ! ? as absolute boundaries,
    with abbreviation / ellipsis / decimal guards via ends_sentence().
    Returns a list of sentence index-ranges: [(start_idx, end_idx), ...]
    where end_idx is exclusive, over script_tokens.
    """
    sentences = []
    start = 0
    for i, (_, raw) in enumerate(script_tokens):
        if ends_sentence(raw):
            sentences.append((start, i + 1))
            start = i + 1
    if start < len(script_tokens):
        sentences.append((start, len(script_tokens)))
    return sentences


def is_comma_break_candidate(token):
    """
    True if this token is a natural comma-pause point.
    Explicitly excludes ellipsis so '...' isn't treated as a comma point
    just because it contains dots.
    """
    stripped = token.rstrip()
    if stripped.endswith("...") or stripped.endswith("…"):
        return False
    return stripped.endswith(",")


def is_soft_punct_break_candidate(token):
    """
    True if this token ends with ':' or a closing quote (" or ' or
    the curly variants) that isn't also an ellipsis/sentence end.
    Used for a SEPARATE, slightly higher pause bar than a plain comma
    (SOFT_PUNCT_PAUSE), because these marks are less reliably a full
    breath-pause than a comma is -- e.g. a colon before a short quoted
    line, or the closing quote right after it.
    """
    stripped = token.rstrip()
    if not stripped:
        return False
    if stripped.endswith("...") or stripped.endswith("…"):
        return False
    if ends_sentence(stripped):
        # Already a sentence end (e.g. quote closing with a period inside
        # like 'happening."') -- that boundary is handled at the sentence
        # level already, don't double-count it here.
        return False
    return stripped.endswith((":", '"', "”", "'", "’"))


def balance(words):
    return " ".join(words)


# ---------------------------------------------------------------------------
# 1. Load script + TextGrid, align words 1:1
# ---------------------------------------------------------------------------

def align_words(script_text, textgrid_path):
    """
    Returns (mapped, script_tokens, match_stats).

    mapped: list of dicts, one per TextGrid word, in order:
        { "start": float, "end": float, "text": str, "script_idx": int|None }
    match_stats: dict with alignment self-check numbers (see point 2).
    """
    script_tokens = tokenize_script(script_text)

    tg = textgrid.openTextgrid(textgrid_path, includeEmptyIntervals=False)
    tier = tg.getTier("words")
    tg_entries = [(st, et, w) for st, et, w in tier.entries if norm(w)]

    # Lookahead window: how far forward we're willing to scan the script
    # (SEARCH_WINDOW) or look ahead in the TextGrid (RESYNC_LOOKAHEAD) when
    # a word doesn't match immediately. This replaces the old "scan to the
    # end of the whole script" behavior, which was both slow and prone to
    # matching a word to the wrong, much-later occurrence of the same text
    # (e.g. a repeated "you are" phrase) once misaligned.
    SEARCH_WINDOW = 12
    RESYNC_LOOKAHEAD = 6

    mapped = []
    si = 0
    ti = 0
    matched = 0
    skipped_script_tokens = 0
    hyphen_merges = 0
    total_tg_words = len(tg_entries)

    def script_norm_at(idx):
        return script_tokens[idx][0] if 0 <= idx < len(script_tokens) else None

    while ti < len(tg_entries):
        st, et, w = tg_entries[ti]
        nw = norm(w)

        # --- Try 1: direct match within a small forward window ---
        found_at = None
        for lookahead in range(SEARCH_WINDOW):
            idx = si + lookahead
            if idx >= len(script_tokens):
                break
            if script_tokens[idx][0] == nw:
                found_at = idx
                break

        if found_at is not None:
            skipped_script_tokens += (found_at - si)
            tok = script_tokens[found_at][1]
            mapped.append({"start": st, "end": et, "text": tok, "script_idx": found_at})
            matched += 1
            si = found_at + 1
            ti += 1
            continue

        # --- Try 2: hyphenated-word split. The CURRENT script token might
        # be a hyphenated compound (e.g. "half-present.") that the
        # aligner split into two separate TextGrid words ("half", "present").
        # If the current script token contains a hyphen, check whether
        # this TextGrid word plus the next one(s), concatenated, match it. ---
        current_script_norm = script_norm_at(si)
        merged_match = False
        if current_script_norm and "-" in script_tokens[si][1]:
            combined = nw
            j = ti
            # Try combining up to 3 extra TextGrid words (compounds are
            # rarely more than 2-3 parts).
            for extra in range(1, 4):
                if j + extra >= len(tg_entries):
                    break
                next_w = tg_entries[j + extra][2]
                combined += norm(next_w)
                if combined == current_script_norm:
                    end_st, end_et, _ = tg_entries[j + extra]
                    tok = script_tokens[si][1]
                    mapped.append({"start": st, "end": end_et, "text": tok, "script_idx": si})
                    matched += 1
                    hyphen_merges += 1
                    si += 1
                    ti = j + extra + 1
                    merged_match = True
                    break
        if merged_match:
            continue

        # --- Try 3: resync. Look a few words ahead in the TextGrid to see
        # if a LATER TextGrid word matches the current script position --
        # meaning this one TextGrid word (and maybe the next couple) are
        # extra/garbled and should be dropped rather than dragging the
        # whole rest of the file out of sync. ---
        resynced = False
        if current_script_norm:
            for skip in range(1, RESYNC_LOOKAHEAD):
                if ti + skip >= len(tg_entries):
                    break
                if norm(tg_entries[ti + skip][2]) == current_script_norm:
                    resynced = True
                    break
        if resynced:
            # Drop this TextGrid word (and any in between) as unmatched,
            # keeping its own text/timing but no script_idx, then let the
            # next loop iteration pick up the real match.
            mapped.append({"start": st, "end": et, "text": w, "script_idx": None})
            ti += 1
            continue

        # --- Fallback: truly no match nearby in either direction.
        # Keep the TextGrid's own text/timing, don't advance si, and move on. ---
        mapped.append({"start": st, "end": et, "text": w, "script_idx": None})
        ti += 1

    match_rate = (matched / total_tg_words * 100) if total_tg_words else 0.0
    match_stats = {
        "total_tg_words": total_tg_words,
        "matched": matched,
        "match_rate_pct": round(match_rate, 1),
        "skipped_script_tokens": skipped_script_tokens,
        "total_script_tokens": len(script_tokens),
        "hyphen_merges": hyphen_merges,
    }

    return mapped, script_tokens, match_stats


def report_alignment(match_stats):
    """
    Point 2: print a visible alignment self-check so a silent
    misalignment shows up as a number, not just a wrong .srt.
    """
    print("--- Alignment self-check ---")
    print(f"  Script tokens total:   {match_stats['total_script_tokens']}")
    print(f"  TextGrid words total:  {match_stats['total_tg_words']}")
    print(f"  Matched to script:     {match_stats['matched']} "
          f"({match_stats['match_rate_pct']}%)")
    if match_stats.get("hyphen_merges", 0) > 0:
        print(f"  Hyphenated words re-merged: {match_stats['hyphen_merges']} "
              f"(e.g. aligner split \"half-present\" into \"half\" + \"present\", "
              f"recombined automatically)")
    if match_stats["skipped_script_tokens"] > 0:
        print(f"  Script tokens skipped during matching: "
              f"{match_stats['skipped_script_tokens']} "
              f"(TTS/script likely diverge somewhere -- worth spot-checking)")
    if match_stats["match_rate_pct"] < 98.0:
        print("  WARNING: match rate below 98%. Some words in the .srt may "
              "have inherited timestamps from the wrong script word. "
              "Recommend spot-checking the output against the audio.")
    print("-----------------------------")


# ---------------------------------------------------------------------------
# 2. Group aligned words into sentences (absolute boundary, never crossed)
# ---------------------------------------------------------------------------

def group_into_sentences(mapped, script_tokens):
    """
    Uses script_idx (position in the original script) to assign each aligned
    word to a sentence, so sentence boundaries come from the SCRIPT, not from
    pause heuristics. Words that failed to match a script index (script_idx
    is None) are appended to whatever sentence is currently open.
    """
    sentence_ranges = split_sentences(script_tokens)  # [(start_idx, end_idx), ...]

    def sentence_of(idx):
        for si, (s, e) in enumerate(sentence_ranges):
            if s <= idx < e:
                return si
        return None

    sentences = [[] for _ in sentence_ranges]
    current_sentence = 0

    for w in mapped:
        if w["script_idx"] is not None:
            si = sentence_of(w["script_idx"])
            if si is not None:
                current_sentence = si
        sentences[current_sentence].append(w)

    # Drop any empty sentences (can happen if a script sentence had 0 aligned words)
    return [s for s in sentences if s]


# ---------------------------------------------------------------------------
# 3. Split one sentence's words into subtitle blocks
# ---------------------------------------------------------------------------

def find_best_split(words, allow_fallback=False):
    """
    Given a list of aligned words belonging to ONE sentence, find the single
    best index to split on, i.e. return i such that block = words[:i] / words[i:].

    Priority:
      1. Comma or soft-punct (":" / closing quote) candidates, whichever
         has the LONGEST pause after it, among all candidates that clear
         their own bar (COMMA_PAUSE for commas, SOFT_PUNCT_PAUSE for
         ":"/quotes). Comma and soft-punct are pooled together and ranked
         purely by pause length -- neither one wins just for being a comma.
      2. If none qualify, the single longest pause anywhere in the
         sentence, if it's >= PAUSE_THRESHOLD.
      3. If allow_fallback is True (tier 2, only used when a block is way
         over CHAR_LIMIT_HARD and steps 1-2 found nothing): accept the
         longest pause anywhere, even below PAUSE_THRESHOLD, as long as
         it's >= FALLBACK_MIN_PAUSE. This is a last resort so a very long,
         pause-poor sentence doesn't stay glued into one oversized block.
      4. If nothing at all qualifies, return None (caller leaves it as-is).
    """
    punct_candidates = []   # commas + soft-punct pooled, ranked by pause length
    pause_candidates = []
    any_pause_candidates = []  # every gap, for tier-2 fallback

    for i in range(len(words) - 1):
        pause = words[i + 1]["start"] - words[i]["end"]
        if pause < 0:
            pause = 0

        text = words[i]["text"]
        if is_comma_break_candidate(text) and pause >= COMMA_PAUSE:
            punct_candidates.append((pause, i + 1))
        elif is_soft_punct_break_candidate(text) and pause >= SOFT_PUNCT_PAUSE:
            punct_candidates.append((pause, i + 1))

        if pause >= PAUSE_THRESHOLD:
            pause_candidates.append((pause, i + 1))

        any_pause_candidates.append((pause, i + 1))

    if punct_candidates:
        punct_candidates.sort(key=lambda x: -x[0])
        return punct_candidates[0][1]

    if pause_candidates:
        pause_candidates.sort(key=lambda x: -x[0])
        return pause_candidates[0][1]

    if allow_fallback and any_pause_candidates:
        any_pause_candidates.sort(key=lambda x: -x[0])
        best_pause, best_idx = any_pause_candidates[0]
        if best_pause >= FALLBACK_MIN_PAUSE:
            return best_idx

    return None


def block_char_len(words):
    """Character length of a block if joined with spaces (point 3)."""
    return len(balance([w["text"] for w in words]))


def split_sentence_into_blocks(words):
    """
    Recursively split a sentence's word list into subtitle blocks.
    - Never crosses the sentence boundary.
    - Uses find_best_split (pause/comma/soft-punct driven) first.
    - MAX_WORDS and CHAR_LIMIT are soft triggers for "this block should
      look for a split point" -- they do not force a cut by themselves.
    - CHAR_LIMIT_HARD is a second, higher bar: once a block is THIS long,
      tier-2 fallback splitting kicks in (find_best_split(..., allow_fallback=True)),
      accepting a weaker pause as a last resort so an oversized block
      doesn't stay glued together just because no comma/colon/quote pause
      was long enough. Below CHAR_LIMIT_HARD, the original "no bad cuts"
      principle still applies untouched.
    """
    char_len = block_char_len(words)
    needs_split_check = len(words) > MAX_WORDS or char_len > CHAR_LIMIT
    needs_hard_fallback = char_len > CHAR_LIMIT_HARD

    if not needs_split_check:
        # Short enough already; still split on an obvious long pause so
        # mid-sentence dramatic pauses aren't glued together.
        split_at = find_best_split(words)
        if split_at is None:
            return [words]
        left, right = words[:split_at], words[split_at:]
        return split_sentence_into_blocks(left) + split_sentence_into_blocks(right)

    split_at = find_best_split(words)
    if split_at is None:
        if needs_hard_fallback:
            # Tier 2: block is well over CHAR_LIMIT_HARD and no normal
            # break point exists. Accept a weaker pause as a last resort
            # rather than leaving an oversized block glued together.
            split_at = find_best_split(words, allow_fallback=True)
        if split_at is None:
            # Still nothing usable, even at the lowered bar: leave as-is.
            return [words]

    left, right = words[:split_at], words[split_at:]
    return split_sentence_into_blocks(left) + split_sentence_into_blocks(right)


# ---------------------------------------------------------------------------
# 4. Build final blocks (start, end, word list) across the whole file
# ---------------------------------------------------------------------------

def build_blocks(sentences):
    blocks = []
    for sent_words in sentences:
        for sub in split_sentence_into_blocks(sent_words):
            if not sub:
                continue
            start = sub[0]["start"]
            end = sub[-1]["end"]
            text_words = [w["text"] for w in sub]
            blocks.append([start, end, text_words])
    return blocks


def apply_tail_padding(blocks):
    for i in range(len(blocks) - 1):
        blocks[i][1] = min(blocks[i][1] + TAIL_PAD, blocks[i + 1][0] - GAP_BEFORE_NEXT)
        if blocks[i][1] < blocks[i][0]:
            blocks[i][1] = max(blocks[i][0], blocks[i][1])
    return blocks


def check_reading_speed(blocks):
    """
    Point 4: flag (don't auto-fix) any block whose characters-per-second
    rate is above MAX_READING_CPS. Timing comes from the actual audio, so
    we don't stretch it automatically -- that would desync from the voice.
    This only prints a report for manual review.
    """
    flagged = []
    for idx, (st, et, words) in enumerate(blocks, 1):
        duration = et - st
        if duration <= 0:
            continue
        char_len = len(balance(words))
        cps = char_len / duration
        if cps > MAX_READING_CPS:
            flagged.append((idx, cps, char_len, duration, balance(words)))

    if flagged:
        print(f"--- Reading speed check: {len(flagged)} block(s) above "
              f"{MAX_READING_CPS} chars/sec ---")
        for idx, cps, char_len, duration, text in flagged:
            preview = text if len(text) <= 60 else text[:57] + "..."
            print(f"  Block {idx}: {cps:.1f} cps ({char_len} chars / "
                  f"{duration:.2f}s) -- \"{preview}\"")
        print("  (These are flagged for review only; timing was not changed,")
        print("   since it must stay matched to the actual audio.)")
        print("-----------------------------")
    return flagged


# ---------------------------------------------------------------------------
# 5. Write SRT
# ---------------------------------------------------------------------------

def write_srt(blocks, path):
    with open(path, "w", encoding="utf-8") as f:
        for idx, (st, et, words) in enumerate(blocks, 1):
            f.write(f"{idx}\n")
            f.write(f"{fmt(st)} --> {fmt(et)}\n")
            f.write(balance(words))
            f.write("\n\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) != 4:
        print("python smart_srt_v4_1.py script.txt input.TextGrid output.srt")
        sys.exit(1)

    script_path, tg_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    script_text = open(script_path, encoding="utf-8").read()

    mapped, script_tokens, match_stats = align_words(script_text, tg_path)
    report_alignment(match_stats)

    sentences = group_into_sentences(mapped, script_tokens)
    blocks = build_blocks(sentences)
    blocks = apply_tail_padding(blocks)

    check_reading_speed(blocks)

    write_srt(blocks, out_path)
    print(f"Done. {len(blocks)} blocks written to {out_path}")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------
# NOTES
# ---------------------------------------------------------------------------
# 1. Abbreviation list (ABBREVIATIONS) is a fixed set, not exhaustive. Nếu
#    script hay dùng một viết tắt khác không có trong danh sách (ví dụ tên
#    riêng dạng "Ven." cho "Venerable"), thêm trực tiếp vào set này.
#
# 2. Alignment self-check chỉ IN RA cảnh báo, không tự sửa gì. Match rate
#    dưới 98% là dấu hiệu nên nghe lại audio đối chiếu script ở đoạn nghi
#    ngờ, đặc biệt nếu "skipped_script_tokens" cao bất thường.
#
# 3. CHAR_LIMIT=42 là chuẩn phổ biến (Netflix dùng ~42 ký tự/dòng cho tiếng
#    Anh). Vì đây vẫn là soft cap (không force-cut), một số block có thể
#    vẫn vượt giới hạn này nếu không tìm được điểm cắt tốt -- đúng nguyên
#    tắc "thà để dài còn hơn cắt xấu" bạn đã chọn từ đầu.
#
# 4. Reading speed check (MAX_READING_CPS=20) chỉ CẢNH BÁO, không tự kéo
#    dài thời gian hiển thị, vì thời gian hiển thị phải khớp với audio thật.
#    Nếu bạn muốn, có thể thêm bước thứ hai: khi bị flag, thử gộp với block
#    liền kề (nếu cùng câu) để giảm số lần chuyển cảnh -- nhưng việc này
#    ảnh hưởng tới nhịp đọc nên chưa bật mặc định, cho tôi biết nếu muốn.
#
# 5. Ellipsis: "..." bây giờ không bị tính là 3 lần kết câu, và cũng không
#    bị tính là điểm ngắt kiểu dấu phẩy. Nó chỉ được xử lý như một từ bình
#    thường trong câu -- điểm cắt (nếu cần) vẫn dựa vào pause thực tế đo
#    được quanh vị trí đó, không dựa vào ký tự "...".

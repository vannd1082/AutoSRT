"""
smart_srt_v4_2.py (legacy mode)
TextGrid -> SRT conversion, called by AutoSRT.bat with 3 arguments.

All files live together in one folder (script .py/.bat, script .txt,
audio .wav/.mp3, .TextGrid, .srt) — no input\\/output\\ subfolders.

AutoSRT.bat handles:
    1. Finding the audio/txt/py files in the current folder.
    2. Converting MP3 -> WAV via ffmpeg (if needed).
    3. Running "mfa align" to produce the .TextGrid.

This script only handles the last step:
    4. TextGrid -> SRT using the v4.1 Smart SRT logic.

Usage:

    python smart_srt_v4_2.py SCRIPT.txt AUDIO.TextGrid OUTPUT.srt


Language detection (Vietnamese / German / English) is still run and
printed for information, but does NOT select the MFA dictionary or
acoustic model in this mode — AutoSRT.bat already chose those before
the TextGrid was produced.
"""

import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Optional dependency
# ---------------------------------------------------------------------------

try:
    from praatio import textgrid
except ImportError:
    print("ERROR: praatio is not installed.")
    print("Install it with:")
    print("    pip install praatio")
    sys.exit(1)


# ---------------------------------------------------------------------------
# MFA language configuration
# ---------------------------------------------------------------------------

LANGUAGE_CONFIG = {

    "vietnamese": {
        "name": "Vietnamese",
        "dictionary": "vietnamese_ho_chi_minh_city_mfa",
        "acoustic": "vietnamese_mfa",
        "g2p": "vietnamese_ho_chi_minh_city_mfa",
        "needs_g2p": True,
    },

    "german": {
        "name": "German",
        "dictionary": "german_mfa",
        "acoustic": "german_mfa",
        "g2p": None,
        "needs_g2p": False,
    },

    "english": {
        "name": "English",
        "dictionary": "english_us_arpa",
        "acoustic": "english_us_arpa",
        "g2p": None,
        "needs_g2p": False,
    },
}


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

PAUSE_THRESHOLD = 0.50
COMMA_PAUSE = 0.20
SOFT_PUNCT_PAUSE = 0.25

MAX_WORDS = 20

CHAR_LIMIT = 42
CHAR_LIMIT_HARD = 55

FALLBACK_MIN_PAUSE = 0.10

GAP_BEFORE_NEXT = 0.05
TAIL_PAD = 1.2

MAX_READING_CPS = 20

END_PUNCT_CHARS = ".!?"


# Common abbreviations that end in "." but do NOT end a sentence.

ABBREVIATIONS = {
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "Sr.",
    "Jr.",
    "St.",
    "vs.",
    "etc.",
    "e.g.",
    "i.e.",
    "U.S.",
    "U.K.",
    "a.m.",
    "p.m.",
    "Fig.",
    "No.",
    "Vol.",
    "Rev.",
    "Gen.",
    "Col.",
    "Ave.",
}


# ---------------------------------------------------------------------------
# 2. Language detection
# ---------------------------------------------------------------------------

def detect_language(text):

    """
    Lightweight automatic language detector for the two supported
    languages.

    It intentionally uses several signals instead of one keyword,
    because Buddhist scripts may contain English / Pali words inside
    Vietnamese text.
    """

    lower = text.lower()

    # Vietnamese-specific characters
    vietnamese_chars = (
        "ă",
        "â",
        "đ",
        "ê",
        "ô",
        "ơ",
        "ư",
    )

    vietnamese_char_score = sum(
        lower.count(ch)
        for ch in vietnamese_chars
    )

    # Vietnamese common function/content words.
    vietnamese_words = {
        "và",
        "là",
        "của",
        "trong",
        "một",
        "những",
        "người",
        "không",
        "được",
        "cho",
        "này",
        "đó",
        "như",
        "khi",
        "với",
        "từ",
        "theo",
        "thì",
        "có",
        "đã",
        "sẽ",
        "phật",
        "pháp",
        "đạo",
        "đức",
        "chúng",
        "ta",
    }

    german_words = {
        "der",
        "die",
        "das",
        "und",
        "ist",
        "nicht",
        "ein",
        "eine",
        "einer",
        "einem",
        "einen",
        "mit",
        "von",
        "für",
        "auf",
        "im",
        "in",
        "zu",
        "den",
        "dem",
        "des",
        "wie",
        "auch",
        "sich",
        "werden",
        "wird",
        "dass",
        "aber",
        "oder",
        "ich",
        "wir",
        "sie",
    }

    words = re.findall(
        r"[A-Za-zÀ-ỹÄÖÜäöüß]+",
        lower
    )

    vietnamese_word_score = sum(
        1
        for w in words
        if w in vietnamese_words
    )

    german_word_score = sum(
        1
        for w in words
        if w in german_words
    )

    # English common function/content words.
    english_words = {
        "the",
        "and",
        "is",
        "are",
        "was",
        "were",
        "you",
        "your",
        "this",
        "that",
        "with",
        "for",
        "not",
        "but",
        "have",
        "has",
        "had",
        "will",
        "would",
        "can",
        "could",
        "what",
        "when",
        "where",
        "how",
        "why",
        "there",
        "their",
        "they",
        "we",
        "our",
        "it",
        "its",
        "of",
        "to",
        "in",
        "on",
        "at",
        "as",
        "be",
        "or",
    }

    english_word_score = sum(
        1
        for w in words
        if w in english_words
    )

    # Strong Vietnamese signal from diacritics.
    vietnamese_score = (
        vietnamese_char_score * 3
        + vietnamese_word_score
    )

    german_score = german_word_score

    english_score = english_word_score

    # German umlauts/ß are a strong, unambiguous German signal.
    german_char_score = sum(
        lower.count(ch)
        for ch in ("ä", "ö", "ü", "ß")
    )

    german_score += german_char_score * 3

    if (
        vietnamese_score >= german_score
        and vietnamese_score >= english_score
        and vietnamese_score > 0
    ):

        return "vietnamese"

    if (
        german_score > vietnamese_score
        and german_score > english_score
    ):

        return "german"

    if (
        english_score > vietnamese_score
        and english_score > german_score
    ):

        return "english"

    # Tie-breaker:
    # Vietnamese scripts commonly contain ă/â/đ/ê/ô/ơ/ư.
    if vietnamese_char_score > 0:
        return "vietnamese"

    if german_char_score > 0:
        return "german"

    # Otherwise default to English — Buddhist Wisdom (EN) is the
    # primary channel and plain scripts with no VN/DE signal at all
    # are almost always English.
    return "english"


# ---------------------------------------------------------------------------
# Smart SRT helpers
# ---------------------------------------------------------------------------

def fmt(t):

    total_ms = int(
        round(t * 1000)
    )

    h, rem_ms = divmod(
        total_ms,
        3600 * 1000
    )

    m, rem_ms = divmod(
        rem_ms,
        60 * 1000
    )

    s, ms = divmod(
        rem_ms,
        1000
    )

    return (
        f"{h:02}:{m:02}:{s:02},{ms:03}"
    )


def norm(w):

    """
    v4.2:
    Preserve Unicode letters.

    This is important for:
        Vietnamese
        German umlauts
        ß
        other non-ASCII alphabetic characters

    Punctuation is removed while letters, numbers and apostrophes remain.
    """

    return "".join(
        ch
        for ch in w.lower()
        if ch.isalnum()
        or ch == "'"
    )


def tokenize_script(text):

    out = []

    for tok in re.findall(
        r"\S+",
        text
    ):

        n = norm(tok)

        if n:

            out.append(
                (
                    n,
                    tok
                )
            )

    return out


def is_decimal_number(token):

    return bool(
        re.match(
            r"^\d+\.\d+",
            token
        )
    )


def ends_sentence(token):

    stripped = token.rstrip()

    if not stripped:
        return False

    if (
        stripped.endswith("...")
        or stripped.endswith("…")
    ):

        return False

    if stripped in ABBREVIATIONS:

        return False

    if (
        is_decimal_number(stripped)
        and stripped[-1] == "."
    ):

        return False

    last_char = stripped[-1]

    return (
        last_char
        in END_PUNCT_CHARS
    )


def split_sentences(script_tokens):

    sentences = []

    start = 0

    for i, (_, raw) in enumerate(
        script_tokens
    ):

        if ends_sentence(raw):

            sentences.append(
                (
                    start,
                    i + 1
                )
            )

            start = i + 1

    if start < len(script_tokens):

        sentences.append(
            (
                start,
                len(script_tokens)
            )
        )

    return sentences


def is_comma_break_candidate(token):

    stripped = token.rstrip()

    if (
        stripped.endswith("...")
        or stripped.endswith("…")
    ):

        return False

    return stripped.endswith(",")


def is_soft_punct_break_candidate(token):

    stripped = token.rstrip()

    if not stripped:
        return False

    if (
        stripped.endswith("...")
        or stripped.endswith("…")
    ):

        return False

    if ends_sentence(stripped):

        return False

    return stripped.endswith(
        (
            ":",
            '"',
            "”",
            "'",
            "’",
        )
    )


def balance(words):

    return " ".join(words)


# ---------------------------------------------------------------------------
# 6. Align TextGrid words to script words
# ---------------------------------------------------------------------------

def align_words(
    script_text,
    textgrid_path
):

    script_tokens = tokenize_script(
        script_text
    )

    tg = textgrid.openTextgrid(
        str(textgrid_path),
        includeEmptyIntervals=False
    )

    tier = tg.getTier(
        "words"
    )

    tg_entries = [
        (st, et, w)
        for st, et, w in tier.entries
        if norm(w)
    ]

    SEARCH_WINDOW = 12
    RESYNC_LOOKAHEAD = 6

    mapped = []

    si = 0
    ti = 0

    matched = 0
    skipped_script_tokens = 0
    hyphen_merges = 0

    total_tg_words = len(
        tg_entries
    )

    def script_norm_at(idx):

        if (
            0 <= idx
            < len(script_tokens)
        ):

            return script_tokens[
                idx
            ][0]

        return None

    while ti < len(tg_entries):

        st, et, w = tg_entries[ti]

        nw = norm(w)

        # ---------------------------------------------------------------
        # Try 1: direct match
        # ---------------------------------------------------------------

        found_at = None

        for lookahead in range(
            SEARCH_WINDOW
        ):

            idx = si + lookahead

            if idx >= len(
                script_tokens
            ):

                break

            if (
                script_tokens[idx][0]
                == nw
            ):

                found_at = idx

                break

        if found_at is not None:

            skipped_script_tokens += (
                found_at - si
            )

            tok = script_tokens[
                found_at
            ][1]

            mapped.append(
                {
                    "start": st,
                    "end": et,
                    "text": tok,
                    "script_idx": found_at,
                }
            )

            matched += 1

            si = found_at + 1
            ti += 1

            continue

        # ---------------------------------------------------------------
        # Try 2: hyphenated compound
        # ---------------------------------------------------------------

        current_script_norm = (
            script_norm_at(si)
        )

        merged_match = False

        if (
            current_script_norm
            and si < len(script_tokens)
            and "-"
            in script_tokens[si][1]
        ):

            combined = nw
            j = ti

            for extra in range(
                1,
                4
            ):

                if (
                    j + extra
                    >= len(tg_entries)
                ):

                    break

                next_w = tg_entries[
                    j + extra
                ][2]

                combined += norm(
                    next_w
                )

                if (
                    combined
                    == current_script_norm
                ):

                    end_st, end_et, _ = (
                        tg_entries[
                            j + extra
                        ]
                    )

                    tok = script_tokens[
                        si
                    ][1]

                    mapped.append(
                        {
                            "start": st,
                            "end": end_et,
                            "text": tok,
                            "script_idx": si,
                        }
                    )

                    matched += 1
                    hyphen_merges += 1

                    si += 1

                    ti = (
                        j
                        + extra
                        + 1
                    )

                    merged_match = True

                    break

        if merged_match:

            continue

        # ---------------------------------------------------------------
        # Try 3: resync
        # ---------------------------------------------------------------

        resynced = False

        if current_script_norm:

            for skip in range(
                1,
                RESYNC_LOOKAHEAD
            ):

                if (
                    ti + skip
                    >= len(tg_entries)
                ):

                    break

                if (
                    norm(
                        tg_entries[
                            ti + skip
                        ][2]
                    )
                    == current_script_norm
                ):

                    resynced = True

                    break

        if resynced:

            mapped.append(
                {
                    "start": st,
                    "end": et,
                    "text": w,
                    "script_idx": None,
                }
            )

            ti += 1

            continue

        # ---------------------------------------------------------------
        # Fallback
        # ---------------------------------------------------------------

        mapped.append(
            {
                "start": st,
                "end": et,
                "text": w,
                "script_idx": None,
            }
        )

        ti += 1

    match_rate = (
        matched
        / total_tg_words
        * 100
        if total_tg_words
        else 0.0
    )

    match_stats = {
        "total_tg_words":
            total_tg_words,

        "matched":
            matched,

        "match_rate_pct":
            round(
                match_rate,
                1
            ),

        "skipped_script_tokens":
            skipped_script_tokens,

        "total_script_tokens":
            len(script_tokens),

        "hyphen_merges":
            hyphen_merges,
    }

    return (
        mapped,
        script_tokens,
        match_stats,
    )


# ---------------------------------------------------------------------------
# 7. Alignment report
# ---------------------------------------------------------------------------

def report_alignment(
    match_stats
):

    print()
    print(
        "--- Alignment self-check ---"
    )

    print(
        f"  Script tokens total:   "
        f"{match_stats['total_script_tokens']}"
    )

    print(
        f"  TextGrid words total:  "
        f"{match_stats['total_tg_words']}"
    )

    print(
        f"  Matched to script:     "
        f"{match_stats['matched']} "
        f"({match_stats['match_rate_pct']}%)"
    )

    if match_stats.get(
        "hyphen_merges",
        0
    ) > 0:

        print(
            f"  Hyphenated words "
            f"re-merged: "
            f"{match_stats['hyphen_merges']}"
        )

    if (
        match_stats[
            "skipped_script_tokens"
        ] > 0
    ):

        print(
            f"  Script tokens skipped "
            f"during matching: "
            f"{match_stats['skipped_script_tokens']}"
        )

    if (
        match_stats[
            "match_rate_pct"
        ] < 98.0
    ):

        print(
            "  WARNING: match rate "
            "below 98%."
        )

        print(
            "  Some words in the .srt "
            "may have inherited "
            "timestamps from the wrong "
            "script word."
        )

        print(
            "  Recommend spot-checking "
            "the output against audio."
        )

    print(
        "-----------------------------"
    )


# ---------------------------------------------------------------------------
# 8. Group aligned words into sentences
# ---------------------------------------------------------------------------

def group_into_sentences(
    mapped,
    script_tokens
):

    sentence_ranges = split_sentences(
        script_tokens
    )

    def sentence_of(idx):

        for si, (
            s,
            e
        ) in enumerate(
            sentence_ranges
        ):

            if s <= idx < e:

                return si

        return None

    sentences = [
        []
        for _ in sentence_ranges
    ]

    current_sentence = 0

    for w in mapped:

        if w["script_idx"] is not None:

            si = sentence_of(
                w["script_idx"]
            )

            if si is not None:

                current_sentence = si

        sentences[
            current_sentence
        ].append(w)

    return [
        s
        for s in sentences
        if s
    ]


# ---------------------------------------------------------------------------
# 9. Find best subtitle split
# ---------------------------------------------------------------------------

def find_best_split(
    words,
    allow_fallback=False
):

    punct_candidates = []
    pause_candidates = []
    any_pause_candidates = []

    for i in range(
        len(words) - 1
    ):

        pause = (
            words[i + 1]["start"]
            - words[i]["end"]
        )

        if pause < 0:

            pause = 0

        text = words[i]["text"]

        if (
            is_comma_break_candidate(
                text
            )
            and pause >= COMMA_PAUSE
        ):

            punct_candidates.append(
                (
                    pause,
                    i + 1
                )
            )

        elif (
            is_soft_punct_break_candidate(
                text
            )
            and pause
            >= SOFT_PUNCT_PAUSE
        ):

            punct_candidates.append(
                (
                    pause,
                    i + 1
                )
            )

        if pause >= PAUSE_THRESHOLD:

            pause_candidates.append(
                (
                    pause,
                    i + 1
                )
            )

        any_pause_candidates.append(
            (
                pause,
                i + 1
            )
        )

    if punct_candidates:

        punct_candidates.sort(
            key=lambda x: -x[0]
        )

        return punct_candidates[0][1]

    if pause_candidates:

        pause_candidates.sort(
            key=lambda x: -x[0]
        )

        return pause_candidates[0][1]

    if (
        allow_fallback
        and any_pause_candidates
    ):

        any_pause_candidates.sort(
            key=lambda x: -x[0]
        )

        best_pause, best_idx = (
            any_pause_candidates[0]
        )

        if (
            best_pause
            >= FALLBACK_MIN_PAUSE
        ):

            return best_idx

    return None


def block_char_len(words):

    return len(
        balance(
            [
                w["text"]
                for w in words
            ]
        )
    )


# ---------------------------------------------------------------------------
# 10. Split sentence into subtitle blocks
# ---------------------------------------------------------------------------

def split_sentence_into_blocks(
    words
):

    char_len = block_char_len(
        words
    )

    needs_split_check = (
        len(words) > MAX_WORDS
        or char_len > CHAR_LIMIT
    )

    needs_hard_fallback = (
        char_len > CHAR_LIMIT_HARD
    )

    if not needs_split_check:

        split_at = find_best_split(
            words
        )

        if split_at is None:

            return [words]

        left = words[:split_at]
        right = words[split_at:]

        return (
            split_sentence_into_blocks(
                left
            )
            +
            split_sentence_into_blocks(
                right
            )
        )

    split_at = find_best_split(
        words
    )

    if split_at is None:

        if needs_hard_fallback:

            split_at = find_best_split(
                words,
                allow_fallback=True
            )

        if split_at is None:

            return [words]

    left = words[:split_at]
    right = words[split_at:]

    return (
        split_sentence_into_blocks(
            left
        )
        +
        split_sentence_into_blocks(
            right
        )
    )


# ---------------------------------------------------------------------------
# 11. Build final subtitle blocks
# ---------------------------------------------------------------------------

def build_blocks(
    sentences
):

    blocks = []

    for sent_words in sentences:

        for sub in (
            split_sentence_into_blocks(
                sent_words
            )
        ):

            if not sub:

                continue

            start = sub[0]["start"]
            end = sub[-1]["end"]

            text_words = [
                w["text"]
                for w in sub
            ]

            blocks.append(
                [
                    start,
                    end,
                    text_words
                ]
            )

    return blocks


def apply_tail_padding(
    blocks
):

    for i in range(
        len(blocks) - 1
    ):

        blocks[i][1] = min(
            blocks[i][1] + TAIL_PAD,
            blocks[i + 1][0]
            - GAP_BEFORE_NEXT
        )

        if (
            blocks[i][1]
            < blocks[i][0]
        ):

            blocks[i][1] = max(
                blocks[i][0],
                blocks[i][1]
            )

    return blocks


# ---------------------------------------------------------------------------
# 12. Reading speed check
# ---------------------------------------------------------------------------

def check_reading_speed(
    blocks
):

    flagged = []

    for idx, (
        st,
        et,
        words
    ) in enumerate(
        blocks,
        1
    ):

        duration = et - st

        if duration <= 0:

            continue

        char_len = len(
            balance(words)
        )

        cps = (
            char_len
            / duration
        )

        if cps > MAX_READING_CPS:

            flagged.append(
                (
                    idx,
                    cps,
                    char_len,
                    duration,
                    balance(words)
                )
            )

    if flagged:

        print()

        print(
            f"--- Reading speed check: "
            f"{len(flagged)} block(s) above "
            f"{MAX_READING_CPS} chars/sec ---"
        )

        for (
            idx,
            cps,
            char_len,
            duration,
            text
        ) in flagged:

            preview = (
                text
                if len(text) <= 60
                else text[:57] + "..."
            )

            print(
                f'  Block {idx}: '
                f'{cps:.1f} cps '
                f'({char_len} chars / '
                f'{duration:.2f}s) '
                f'-- "{preview}"'
            )

        print(
            "  (Flag only; timing was "
            "not changed.)"
        )

        print(
            "-----------------------------"
        )

    return flagged


# ---------------------------------------------------------------------------
# 13. Write SRT
# ---------------------------------------------------------------------------

def write_srt(
    blocks,
    path
):

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:

        for idx, (
            st,
            et,
            words
        ) in enumerate(
            blocks,
            1
        ):

            f.write(
                f"{idx}\n"
            )

            f.write(
                f"{fmt(st)} --> "
                f"{fmt(et)}\n"
            )

            f.write(
                balance(words)
            )

            f.write(
                "\n\n"
            )


# ---------------------------------------------------------------------------
# 14. Main automatic pipeline
# ---------------------------------------------------------------------------

def main():

    if len(sys.argv) != 4:

        print(
            "smart_srt_v4_2.py (legacy mode)"
        )

        print(
            "Usage:"
        )

        print(
            '  python smart_srt_v4_2.py '
            'SCRIPT.txt AUDIO.TextGrid OUTPUT.srt'
        )

        sys.exit(1)

    txt_path = Path(sys.argv[1])
    textgrid_path = Path(sys.argv[2])
    output_srt = Path(sys.argv[3])

    print()
    print("=" * 70)
    print("SMART SRT v4.2 (legacy mode)")
    print("TextGrid -> SRT")
    print("=" * 70)

    # -----------------------------------------------------------------------
    # Validate paths
    # -----------------------------------------------------------------------

    if not txt_path.exists():

        print()
        print(
            f"ERROR: TXT not found: {txt_path}"
        )

        sys.exit(1)

    if not textgrid_path.exists():

        print()
        print(
            f"ERROR: TextGrid not found: {textgrid_path}"
        )

        sys.exit(1)

    print()
    print(
        "--- Input files ---"
    )

    print(
        f"  TXT:      {txt_path.name}"
    )

    print(
        f"  TextGrid: {textgrid_path.name}"
    )

    print(
        f"  SRT:      {output_srt.name}"
    )

    # -----------------------------------------------------------------------
    # Read script
    # -----------------------------------------------------------------------

    try:

        script_text = txt_path.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError:

        print()
        print(
            "ERROR: TXT must be UTF-8 encoded."
        )

        sys.exit(1)

    except Exception as e:

        print()
        print(
            f"ERROR reading TXT: {e}"
        )

        sys.exit(1)

    # -----------------------------------------------------------------------
    # Language detection (informational only in legacy mode —
    # the .bat file already chose the MFA dictionary/acoustic model
    # used to produce the TextGrid before calling this script).
    # -----------------------------------------------------------------------

    language = detect_language(
        script_text
    )

    config = LANGUAGE_CONFIG[
        language
    ]

    print()
    print(
        "--- Language detection (info only) ---"
    )

    print(
        f"  Detected: {config['name']}"
    )

    # -----------------------------------------------------------------------
    # TextGrid -> SRT
    # -----------------------------------------------------------------------

    try:

        (
            mapped,
            script_tokens,
            match_stats
        ) = align_words(
            script_text,
            textgrid_path
        )

    except Exception as e:

        print()
        print(
            f"ERROR reading TextGrid: {e}"
        )

        sys.exit(1)

    # -----------------------------------------------------------------------
    # Alignment report
    # -----------------------------------------------------------------------

    report_alignment(
        match_stats
    )

    # -----------------------------------------------------------------------
    # Sentence grouping
    # -----------------------------------------------------------------------

    sentences = group_into_sentences(
        mapped,
        script_tokens
    )

    # -----------------------------------------------------------------------
    # Subtitle blocks
    # -----------------------------------------------------------------------

    blocks = build_blocks(
        sentences
    )

    blocks = apply_tail_padding(
        blocks
    )

    # -----------------------------------------------------------------------
    # Reading speed
    # -----------------------------------------------------------------------

    check_reading_speed(
        blocks
    )

    # -----------------------------------------------------------------------
    # Write SRT
    # -----------------------------------------------------------------------

    try:

        write_srt(
            blocks,
            output_srt
        )

    except Exception as e:

        print()
        print(
            f"ERROR writing SRT: {e}"
        )

        sys.exit(1)

    # -----------------------------------------------------------------------
    # Final report
    # -----------------------------------------------------------------------

    print()
    print("=" * 70)
    print("SUCCESS")
    print("=" * 70)

    print(
        f"  SRT:       {output_srt.name}"
    )

    print(
        f"  Subtitles: {len(blocks)} blocks"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()
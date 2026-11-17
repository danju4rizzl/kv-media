"""Find filler words, repeated phrases and restarted sentences in a word-timed transcript.

Pure functions, no I/O except reading the filler list. Ported from the original
kavara-audio-enhancer, including its safety limits (it once deleted 27 s of real content
before the restart rule was tightened).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from kv_media.cuts import Cut
from kv_media.transcribe import Word

DEFAULT_FILLERS_FILE = Path(__file__).with_name("fillers.txt")

MAX_REPEAT_PHRASE_WORDS = 8     # longest repeated phrase looked for
MAX_REPEAT_GAP = 1.5            # a longer pause between identical phrases = deliberate, keep both
RESTART_MIN_OPENER = 3          # shared opening words needed to call it a restart
RESTART_MAX_SECONDS = 12.0      # abandoned attempt must be shorter than this
RESTART_MAX_WORDS = 16
RESTART_MAX_GAP = 3.0
FUZZY_MIN_LENGTH = 5            # shorter words must match exactly
FUZZY_RATIO = 0.85


def normalize(text: str) -> str:
    return re.sub(r"[^\w']+", "", text.lower())


# ---- filler list -------------------------------------------------------------------

def _word_pattern(word: str) -> str:
    """'um' -> 'u+m+' (catches umm, uum, ummmm). A single repeated letter ('mm') -> 'm{2,}'."""
    letters = re.sub(r"(.)\1+", r"\1", normalize(word))
    if not letters:
        return ""
    if len(letters) == 1:
        return re.escape(letters) + "{2,}"
    return "".join(re.escape(ch) + "+" for ch in letters)


def load_fillers(path: Path | None = None) -> list[list[re.Pattern]]:
    """Each entry is a phrase: a list of compiled word patterns."""
    source = path or (Path.cwd() / "fillers.txt" if (Path.cwd() / "fillers.txt").is_file()
                      else DEFAULT_FILLERS_FILE)
    phrases = []
    for raw in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        patterns = [_word_pattern(w) for w in line.split()]
        if all(patterns):
            phrases.append([re.compile(p) for p in patterns])
    return phrases


def fillers_source(path: Path | None = None) -> Path:
    if path:
        return path
    local = Path.cwd() / "fillers.txt"
    return local if local.is_file() else DEFAULT_FILLERS_FILE


def _is_truncated(text: str) -> bool:
    return text.strip().endswith(("-", "–", "—"))     # cut-off word: "wa-"


def find_filler_cuts(words: list[Word], fillers: list[list[re.Pattern]]) -> list[Cut]:
    norm = [normalize(w.text) for w in words]
    cuts: list[Cut] = []
    i = 0
    while i < len(words):
        hit = 0
        for phrase in sorted(fillers, key=len, reverse=True):
            n = len(phrase)
            if i + n <= len(words) and all(p.fullmatch(norm[i + k]) for k, p in enumerate(phrase)):
                hit = n
                break
        if hit:
            span = words[i:i + hit]
            cuts.append(Cut(span[0].start, span[-1].end, "filler", " ".join(w.text for w in span)))
            i += hit
            continue
        if _is_truncated(words[i].text):
            cuts.append(Cut(words[i].start, words[i].end, "filler", words[i].text))
        i += 1
    return [c for c in cuts if c.end > c.start]


# ---- repeats and restarts ---------------------------------------------------------

def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LENGTH:
        return False
    return SequenceMatcher(None, a, b).ratio() >= FUZZY_RATIO


def _content_words(words: list[Word], fillers: list[list[re.Pattern]]) -> list[Word]:
    """Words minus fillers, so 'I just um I just' still counts as a repeat."""
    single = [p[0] for p in fillers if len(p) == 1]
    out = []
    for w in words:
        n = normalize(w.text)
        if n and not _is_truncated(w.text) and not any(p.fullmatch(n) for p in single):
            out.append(w)
    return out


def _text_between(words: list[Word], start: float, end: float) -> str:
    return " ".join(w.text for w in words if w.start >= start - 0.01 and w.end <= end + 0.01)


def find_repeat_cuts(words: list[Word], fillers: list[list[re.Pattern]]) -> list[Cut]:
    """'I just want to... I just want to click here' -> cut the earlier take(s), keep the last."""
    kept = _content_words(words, fillers)
    norm = [normalize(w.text) for w in kept]
    cuts: list[Cut] = []
    i = 0
    while i < len(kept):
        matched = 0
        for n in range(min(MAX_REPEAT_PHRASE_WORDS, (len(kept) - i) // 2), 0, -1):
            first, second = norm[i:i + n], norm[i + n:i + 2 * n]
            if not all(_similar(a, b) for a, b in zip(first[:-1], second[:-1])):
                continue
            # the first take may stop mid-word: "I just wa" -> "I just want"
            if not (second[-1].startswith(first[-1]) or _similar(first[-1], second[-1])):
                continue
            if kept[i + n].start - kept[i + n - 1].end > MAX_REPEAT_GAP:
                continue
            matched = n
            break
        if matched:
            start, end = kept[i].start, kept[i + matched].start
            cuts.append(Cut(start, end, "repeat", _text_between(words, start, end)))
            i += matched          # the second take may itself be repeated
        else:
            i += 1
    return cuts


def _ends_sentence(text: str) -> bool:
    return text.strip().endswith((".", "?", "!"))


def find_restart_cuts(words: list[Word], fillers: list[list[re.Pattern]]) -> list[Cut]:
    """Drop an abandoned, unfinished attempt when the same 3+ word opener is restarted."""
    kept = _content_words(words, fillers)
    norm = [normalize(w.text) for w in kept]
    cuts: list[Cut] = []
    i = 0
    while i < len(kept) - RESTART_MIN_OPENER:
        hit = 0
        for j in range(i + 2, min(i + RESTART_MAX_WORDS, len(kept) - 1) + 1):
            k = 0
            while j + k < len(kept) and i + k < j and _similar(norm[i + k], norm[j + k]):
                k += 1
            if k < RESTART_MIN_OPENER:
                continue
            unfinished = not any(_ends_sentence(w.text) for w in kept[i:j - 1])
            if not unfinished or kept[j].start - kept[i].start > RESTART_MAX_SECONDS:
                continue
            if kept[j].start - kept[j - 1].end > RESTART_MAX_GAP:
                continue
            hit = j
            break
        if hit:
            start, end = kept[i].start, kept[hit].start
            cuts.append(Cut(start, end, "restart", _text_between(words, start, end)))
            i = hit
        else:
            i += 1
    return cuts

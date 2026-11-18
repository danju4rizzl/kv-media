"""AI review of the Markdown transcript: suggests words to cut. Optional, opt-in, reviewed by you.

A cheap Gemini text model reads the numbered transcript (transcript_md) and points at words
that add nothing:
- nonsense    : words that make no sense where they are (misspoken, garbled, half-words,
                a wrong word that is corrected straight away: "on Tuesday, I mean, Thursday")
- false-start : a sentence abandoned and started again
- repeat      : stutters / doubled words ("in the in in the")
- filler      : verbal habits used as filler ("you know", "sort of", "basically")

It QUOTES the words (with the line number) instead of giving positions; kvm finds the quote in
that line and uses the real word timings. These are suggestions: they become cuts only when you
approve them (the wizard asks; direct mode uses --ai-min-confidence). Results are cached, so
re-planning never pays twice. Times come from the transcript, then refine.py aligns the cuts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from kv_media import gemini_text, transcribe, transcript_md
from kv_media.cuts import Cut
from kv_media.transcribe import Word

DEFAULT_MODEL = "gemini-2.5-flash-lite"      # cheapest, long-available text model; GEMINI_REVIEW_MODEL
FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash")
PROMPT_VERSION = 1                            # bump when the prompt changes (invalidates the cache)
CHUNK_CHARS = 60_000
MAX_QUOTE_WORDS = 25
MAX_QUOTE_SECONDS = 12.0
MAX_SKIPPED = 2             # words the AI may leave out of a quote and still match
FUZZY_MIN_WORDS = 3         # shorter quotes must match exactly
REASONS = ("nonsense", "false-start", "repeat", "filler")


class ReviewError(RuntimeError):
    pass


@dataclass(frozen=True)
class Suggestion:
    line: int
    remove: str
    reason: str
    confidence: float
    why: str = ""
    before: str = ""          # a few words just before the quote, to tell repeats apart


@dataclass(frozen=True)
class Match:
    """A suggestion found in the transcript."""
    suggestion: Suggestion
    first: int
    last: int
    start: float
    end: float

    def cut(self) -> Cut:
        return Cut(self.start, self.end, f"ai-{self.suggestion.reason}", self.suggestion.remove)

    @property
    def key(self) -> tuple[float, float]:
        return (round(self.start, 2), round(self.end, 2))


def model_name() -> str:
    return (os.environ.get("GEMINI_REVIEW_MODEL") or DEFAULT_MODEL).strip().removeprefix("models/")


# ---- prompt ----------------------------------------------------------------------

def build_prompt(markdown: str) -> str:
    return f"""You are a careful video editor cleaning up a spoken recording. Below is a VERBATIM
transcript, one numbered line per sentence: `[line] (time) words`. `[pause N.Ns]` marks silence.

Find words the speaker should NOT have said, so cutting them makes the speech cleaner:
- "nonsense": words that make no sense where they are: misspoken or garbled words, half-words,
  stray words that break the sentence, or a wrong word corrected right away
  ("on Tuesday, I mean, Thursday" -> remove "on Tuesday, I mean,").
- "false-start": a sentence or phrase abandoned and started again (remove the abandoned part).
- "repeat": stutters and doubled words ("in the in in the" -> remove "in the in").
- "filler": verbal habits used as filler ("you know", "sort of", "basically", "like").

Strict rules:
- After the cut, the sentence must still read naturally and mean the same. If unsure, leave it.
- Never remove names, product names, technical terms, numbers or real content.
- A word that looks odd but may be a TRANSCRIPTION mistake for a sensible word (it sounds similar)
  is NOT nonsense: the speaker probably said it correctly. Leave it.
- Remove as few words as possible. When something is repeated, keep the LAST, complete take.
- Do not cut long passages: at most {MAX_QUOTE_WORDS} words per suggestion.
- "remove" must be copied EXACTLY from that one line (same words, same order; punctuation may differ).
- If the same words appear more than once in the line, set "before" to the 1-3 words right before
  the part to remove.
- "confidence": 0.0-1.0 that cutting is clearly right.

Reply with JSON only:
{{"cuts": [{{"line": 2, "remove": "in the in", "before": "link", "reason": "repeat",
            "confidence": 0.95, "why": "stutter"}}]}}
Return {{"cuts": []}} if nothing should be cut.

Transcript:
{markdown}
"""


def _chunks(markdown: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split on line boundaries; each chunk keeps the header so the format stays explained."""
    head, sep, body = markdown.partition("\n---\n")
    if not sep:
        head, body = "", markdown
    blocks = [b for b in body.split("\n\n") if b.strip()]
    chunks, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) > limit:
            chunks.append(current)
            current = ""
        current += block + "\n\n"
    if current:
        chunks.append(current)
    return [f"{head}\n---\n\n{c}" if head else c for c in chunks] or [markdown]


# ---- parsing ---------------------------------------------------------------------

def _json(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ReviewError("The AI review did not return JSON.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise ReviewError(f"The AI review returned invalid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


def parse_suggestions(raw: str) -> list[Suggestion]:
    out = []
    for item in _json(raw).get("cuts") or []:
        if not isinstance(item, dict):
            continue
        try:
            line = int(item.get("line"))
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            continue
        remove = re.sub(r"\s+", " ", str(item.get("remove") or "")).strip()
        reason = str(item.get("reason") or "").strip().lower().replace("_", "-").replace(" ", "-")
        if not remove or reason not in REASONS:
            continue
        out.append(Suggestion(line, remove, reason, max(0.0, min(1.0, confidence)),
                              str(item.get("why") or "").strip(), str(item.get("before") or "").strip()))
    return out


# ---- matching quotes back to word timings -------------------------------------------

def _tokens(text: str) -> list[str]:
    text = re.sub(r"\[pause [^\]]*\]", " ", text)
    return [t for t in (re.sub(r"[^\w']+", "", w.lower()) for w in text.split()) if t]


def _is_subsequence(quote: list[str], span: list[str]) -> bool:
    it = iter(span)
    return all(token in it for token in quote)


def _fuzzy_hits(quote: list[str], norm: list[str], first: int, last: int) -> list[tuple[int, int]]:
    """Spans that contain the quote in order with up to MAX_SKIPPED extra words in between.

    The AI often drops a word when quoting ("we we if" for "we can we if", "P PAW" for
    "P uh PAW"). The span must start and end on the quote's first and last words.
    """
    if len(quote) < FUZZY_MIN_WORDS:
        return []
    hits = []
    for i in range(first, last + 1):
        if norm[i] != quote[0]:
            continue
        for j in range(i + len(quote), min(last, i + len(quote) - 1 + MAX_SKIPPED) + 1):
            if norm[j] == quote[-1] and _is_subsequence(quote, norm[i:j + 1]):
                hits.append((i, j))
                break
    return hits


def match(suggestions: list[Suggestion], words: list[Word], max_words: int | None = -1,
          max_seconds: float | None = -1) -> list[Match]:
    """Find each quote in its line. Limits default (-1) to MAX_QUOTE_WORDS / MAX_QUOTE_SECONDS;
    None = no limit (your own transcript edits)."""
    max_words = MAX_QUOTE_WORDS if max_words == -1 else max_words
    max_seconds = MAX_QUOTE_SECONDS if max_seconds == -1 else max_seconds
    lines = {l.number: l for l in transcript_md.split_lines(words)}
    norm = [re.sub(r"[^\w']+", "", w.text.lower()) for w in words]
    found: list[Match] = []
    for s in suggestions:
        line = lines.get(s.line)
        quote = _tokens(s.remove)
        if line is None or not quote or (max_words is not None and len(quote) > max_words):
            continue
        before = _tokens(s.before)
        n = len(quote)
        hits = [(i, i + n - 1) for i in range(line.first, line.last - n + 2) if norm[i:i + n] == quote]
        if not hits:
            hits = _fuzzy_hits(quote, norm, line.first, line.last)
        if not hits:
            continue
        k = len(before)
        preferred = [h for h in hits if k and h[0] - k >= 0 and norm[h[0] - k:h[0]] == before]
        i, j = (preferred or hits)[0]
        start, end = words[i].start, words[j].end
        if (max_seconds is not None and end - start > max_seconds) or j - i + 1 == len(words):
            continue
        found.append(Match(s, i, j, start, end))
    # drop exact duplicates
    unique: dict[tuple[float, float], Match] = {}
    for m in found:
        unique.setdefault(m.key, m)
    return sorted(unique.values(), key=lambda m: m.start)


# ---- Gemini call (cached) -----------------------------------------------------------

def _cache_path(markdown: str) -> Path:
    digest = hashlib.sha1(f"{markdown}|{model_name()}|{PROMPT_VERSION}".encode("utf-8")).hexdigest()[:20]
    return transcribe.cache_dir().parent / "reviews" / f"{digest}.json"


def load_cached(markdown: str) -> list[Suggestion] | None:
    try:
        data = json.loads(_cache_path(markdown).read_text(encoding="utf-8"))
        return [Suggestion(**s) for s in data["suggestions"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cached(markdown: str, suggestions: list[Suggestion]) -> None:
    path = _cache_path(markdown)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model_name(), "suggestions": [asdict(s) for s in suggestions]}),
                    encoding="utf-8")


def review_markdown(words: list[Word], duration: float) -> str:
    """The transcript sent for review (a fixed name in the header, so renames keep the cache)."""
    return transcript_md.build(words, Path("recording"), duration)


def suggest(words: list[Word], duration: float, api_key: str | None) -> tuple[list[Suggestion], bool]:
    """(suggestions, from_cache). Needs ``api_key`` only when not cached."""
    markdown = review_markdown(words, duration)
    cached = load_cached(markdown)
    if cached is not None:
        return cached, True
    if not api_key:
        raise ReviewError("The AI review needs a Gemini API key.")

    from google import genai

    client = genai.Client(api_key=api_key)
    models = gemini_text.chain(model_name(), FALLBACK_MODELS)
    suggestions: list[Suggestion] = []
    for chunk in _chunks(markdown):
        try:
            found, _model = gemini_text.generate_json(
                client, models, build_prompt(chunk), 0.2, "AI review", parse_suggestions)
        except Exception as exc:  # API / network errors: show the message, never the key
            raise ReviewError(f"AI review request failed: {exc}") from exc
        suggestions += found
    save_cached(markdown, suggestions)
    return suggestions, False

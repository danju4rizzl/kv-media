"""AI title + SEO pack from the transcript (Gemini text model).

Gemini writes title suggestions; kvm only cleans them and turns the chosen one into a
safe, SEO-friendly filename. The transcript is never changed here.

Best practices baked into the prompt:
- Primary keyword (what people would search for) in the first ~40 characters.
- 40-60 characters, so the title is not cut off in YouTube / Google results.
- One distinct hook per title: curiosity gap, how-to, number/list, benefit/outcome,
  bold claim / contrarian. Ranked best first.
- Honest: the video must deliver what the title promises (no misleading clickbait).
- Title Case, no ALL CAPS, no emojis, no hashtags in titles; same language as the speaker.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from kv_media import gemini_text, transcribe

# Cheapest, long-available model first: the newest models are the ones that are "busy" (503).
DEFAULT_MODEL = "gemini-2.5-flash-lite"
# Tried in order when the model above is busy (503), rate limited, missing or replies badly.
FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash")
MAX_TRANSCRIPT_CHARS = 40_000
TITLE_MAX = 70            # hard cap after cleaning; the prompt asks for <= 60
FILENAME_MAX = 80
STYLES = ("curiosity", "how-to", "list", "benefit", "bold-claim")


class TitleError(RuntimeError):
    pass


@dataclass(frozen=True)
class Title:
    text: str
    style: str
    why: str = ""


@dataclass
class TitlePack:
    titles: list[Title]
    keyword: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    model: str = ""           # the Gemini model that wrote it


def model_name() -> str:
    return (os.environ.get("GEMINI_TITLE_MODEL") or DEFAULT_MODEL).strip().removeprefix("models/")


# ---- prompt ------------------------------------------------------------------------

def transcript_text(words: list[transcribe.Word], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Plain transcript. Long ones keep the start and the end (where hooks and payoffs live)."""
    text = " ".join(w.text for w in words).strip()
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)].rsplit(" ", 1)[0]
    tail = text[-int(limit * 0.3):].split(" ", 1)[-1]
    return f"{head}\n[... middle of transcript omitted ...]\n{tail}"


def build_prompt(text: str, duration: float, is_video: bool, avoid: list[str] | None = None) -> str:
    kind = "video" if is_video else "audio recording (podcast / voice)"
    minutes = max(1, round(duration / 60))
    avoid_block = ""
    if avoid:
        listed = "\n".join(f"- {t}" for t in avoid)
        avoid_block = f"\nThe user rejected these titles; write clearly different ones:\n{listed}\n"
    return f"""You are an expert YouTube / podcast SEO copywriter.
Write titles for a {minutes}-minute {kind}, based ONLY on its transcript below.

Rules for every title:
- Put the primary search keyword (what a viewer would type to find this) in the first 40 characters.
- 40-60 characters. Never more than 60.
- A real hook that makes people want to click, but HONEST: the recording must deliver it.
- Specific beats vague: use concrete numbers, results, names or tools that appear in the transcript.
- Title Case. No ALL CAPS words, no emojis, no hashtags, no quotation marks, no clickbait lies.
- Same language as the speaker.

Write exactly 5 titles, one per hook style, ranked best first (most likely to get clicks AND search traffic):
- "curiosity": opens a question or gap the recording answers
- "how-to": teaches a clear outcome ("How to ...")
- "list": a number or list ("5 ...", "3 Mistakes ...")
- "benefit": leads with the result or transformation for the viewer
- "bold-claim": a confident or contrarian statement the speaker actually makes
{avoid_block}
Also write an SEO pack:
- "keyword": the single primary search keyword phrase
- "description": 2-3 sentences, keyword in the first sentence, first 150 characters work alone as a summary
- "tags": 10-15 search tags, most specific first
- "hashtags": 3-5 hashtags, each starting with #

Reply with JSON only, exactly this shape:
{{"titles": [{{"title": "...", "style": "curiosity", "why": "one short reason"}}],
 "keyword": "...", "description": "...", "tags": ["..."], "hashtags": ["#..."]}}

Transcript:
\"\"\"
{text}
\"\"\"
"""


# ---- parsing / cleaning ----------------------------------------------------------------

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def clean_title(raw: str) -> str:
    text = _EMOJI.sub("", str(raw or ""))
    text = re.sub(r"\s+#\w+", "", text)                      # hashtags
    text = re.sub(r"\s+", " ", text).strip().strip("\"'“”‘’`*#").strip()
    if len(text) > TITLE_MAX:
        text = text[:TITLE_MAX].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return text


def _json_object(raw: str) -> dict:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        text = text[start:end + 1] if start != -1 and end > start else text
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TitleError(f"Gemini did not return valid JSON for the titles: {exc}") from exc
    if not isinstance(data, dict):
        raise TitleError("Gemini returned an unexpected title format.")
    return data


def parse_pack(raw: str) -> TitlePack:
    data = _json_object(raw)
    titles: list[Title] = []
    seen: set[str] = set()
    for item in data.get("titles") or []:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        text = clean_title(item.get("title", ""))
        if len(text) < 8 or text.lower() in seen:
            continue
        seen.add(text.lower())
        style = str(item.get("style") or "").strip().lower() or "title"
        titles.append(Title(text, style, str(item.get("why") or "").strip()))
    if not titles:
        raise TitleError("Gemini returned no usable titles.")

    def strings(key: str) -> list[str]:
        values = data.get(key) or []
        return [str(v).strip() for v in values if str(v).strip()] if isinstance(values, list) else []

    hashtags = []
    for tag in strings("hashtags"):
        tag = "#" + re.sub(r"[^\w]", "", tag.lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    return TitlePack(
        titles=titles,
        keyword=str(data.get("keyword") or "").strip(),
        description=re.sub(r"\s+", " ", str(data.get("description") or "")).strip(),
        tags=list(dict.fromkeys(strings("tags"))),
        hashtags=hashtags,
    )


# ---- Gemini call ------------------------------------------------------------------

def generate(words: list[transcribe.Word], duration: float, is_video: bool, api_key: str,
             avoid: list[str] | None = None) -> TitlePack:
    text = transcript_text(words)
    if len(text.split()) < 5:
        raise TitleError("The transcript is too short to write a title from.")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = build_prompt(text, duration, is_video, avoid)
    try:
        pack, model = gemini_text.generate_json(
            client, gemini_text.chain(model_name(), FALLBACK_MODELS), prompt, 0.9, "title", parse_pack)
    except Exception as exc:  # API / network errors: show the message, never the key
        raise TitleError(f"Gemini title request failed: {exc}") from exc
    pack.model = model
    return pack


# ---- output ----------------------------------------------------------------------

def filename_slug(title: str, limit: int = FILENAME_MAX) -> str:
    """SEO-friendly, cross-platform filename: lowercase words joined by hyphens."""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).replace("_", "-")
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if len(text) > limit:
        text = text[:limit].rsplit("-", 1)[0]
    return text or "untitled"


def unique_path(path: Path, keep: Path | None = None) -> Path:
    """``path``, or ``name-2.ext``, ``name-3.ext``... if a different file already has that name."""
    candidate, n = path, 2
    while candidate.exists() and candidate != keep:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def unique_pack_dir(base: Path, slug: str, keep: Path | None = None) -> Path:
    """``base / slug``, or ``base / slug-2``... if a different file or folder already has that name."""
    candidate, n = base / slug, 2
    while candidate.exists() and candidate != keep:
        candidate = base / f"{slug}-{n}"
        n += 1
    return candidate


def seo_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_seo.txt")


def build_seo_text(pack: TitlePack, chosen: str) -> str:
    lines = ["kvm SEO pack", "============", "", "Title", f"  {chosen}  ({len(chosen)} chars)", ""]
    if pack.keyword:
        lines += ["Primary keyword", f"  {pack.keyword}", ""]
    if pack.description:
        lines += ["Description", f"  {pack.description}", ""]
    if pack.tags:
        lines += ["Tags (comma separated, paste into YouTube)", f"  {', '.join(pack.tags)}", ""]
    if pack.hashtags:
        lines += ["Hashtags", f"  {' '.join(pack.hashtags)}", ""]
    lines.append("Other title ideas")
    lines += [f"  [{t.style}] {t.text}" for t in pack.titles if t.text != chosen]
    return "\n".join(lines) + "\n"

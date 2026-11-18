"""Word-timed transcription with Gemini. Transcribe ONCE per source file (cached).

Rules carried over from the original (see its AGENTS.md):
- Model gemini-3.5-transcribe, VERBATIM mode (keeps um/ah/repeats), word_timestamp=True.
- The client must pin api_version="v1beta" or the API rejects audio_transcription_config.
- Gemini only transcribes; cut decisions are local code (disfluency.py).
- Retry 429/5xx with backoff. Never report success when every chunk failed.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from kv_media import ffmpeg, ui

DEFAULT_MODEL = "gemini-3.5-transcribe"
CHUNK_SECONDS = 180.0
MAX_WORKERS = 4
RETRY_DELAYS = (20, 40, 70, 100)
CACHE_DIR = Path.home() / ".cache" / "kvm" / "transcripts"


class TranscribeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


def model_name() -> str:
    return (os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).strip().removeprefix("models/")


# ---- cache -------------------------------------------------------------------
# Keyed by the file's CONTENT (size + samples of its bytes), not its path, so the same video
# opened from Windows (C:\...) and WSL (/mnt/c/...), or after a move/rename, is never paid
# for twice. On WSL the Windows user cache is shared too (read and written).

HASH_SAMPLE = 1 << 20      # bytes read from the start, middle and end of the file


def cache_dir() -> Path:
    env = os.environ.get("KVM_CACHE_DIR")
    return Path(env).expanduser() / "transcripts" if env else CACHE_DIR


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False


def shared_cache_dirs() -> list[Path]:
    """Other kvm caches for this user that can hold the same transcripts (WSL -> Windows)."""
    if os.name == "nt" or not _is_wsl():
        return []
    return sorted(p for p in Path("/mnt/c/Users").glob("*/.cache/kvm/transcripts") if p.is_dir())


def _all_cache_dirs() -> list[Path]:
    dirs = [cache_dir()]
    return dirs + [d for d in shared_cache_dirs() if d.resolve() != dirs[0].resolve()]


def content_key(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1(str(size).encode("utf-8"))
    with path.open("rb") as f:
        for offset in sorted({0, max(0, size // 2 - HASH_SAMPLE // 2), max(0, size - HASH_SAMPLE)}):
            f.seek(offset)
            digest.update(f.read(HASH_SAMPLE))
    return digest.hexdigest()


def _cache_digest(info: ffmpeg.MediaInfo) -> str:
    return hashlib.sha1(f"{content_key(info.path)}|{model_name()}".encode("utf-8")).hexdigest()[:16]


def _cache_name(info: ffmpeg.MediaInfo) -> str:
    """The name is only for humans; lookups use the content digest after the last '-c'."""
    return f"{info.path.stem[:40]}-c{_cache_digest(info)}.json"


def _legacy_cache_path(info: ffmpeg.MediaInfo) -> Path:
    """Before v0.3 the key was the absolute path + size + mtime (still read, then migrated)."""
    stat = info.path.stat()
    ident = f"{info.path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|{model_name()}"
    digest = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"{info.path.stem[:40]}-{digest}.json"


def _read(path: Path) -> list[Word] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Word(**w) for w in data["words"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_cached(info: ffmpeg.MediaInfo) -> list[Word] | None:
    digest = _cache_digest(info)
    for folder in _all_cache_dirs():
        for path in sorted(folder.glob(f"*-c{digest}.json")):
            words = _read(path)
            if words is not None:
                return words
    words = _read(_legacy_cache_path(info))
    if words is not None:
        save_cached(info, words)          # migrate, so Windows/WSL can share it from now on
    return words


def save_cached(info: ffmpeg.MediaInfo, words: list[Word]) -> None:
    payload = json.dumps({"source": str(info.path), "model": model_name(),
                          "words": [asdict(w) for w in words]})
    name = _cache_name(info)
    for i, folder in enumerate(_all_cache_dirs()):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(payload, encoding="utf-8")
        except OSError:
            if i == 0:
                raise                       # the main cache must work; shared ones are a bonus


# ---- response parsing ----------------------------------------------------------

def _seconds(value) -> float | None:
    if value is None:
        return None
    if hasattr(value, "total_seconds"):
        return value.total_seconds()
    try:
        return float(str(value).strip().rstrip("sS"))
    except ValueError:
        return None


def extract_words(response, offset: float, duration: float | None = None) -> list[Word]:
    """Word timings from a transcribe response, shifted to absolute time."""
    words: list[Word] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            transcription = getattr(part, "audio_transcription", None)
            for item in getattr(transcription, "words", None) or []:
                text = (getattr(item, "word", "") or "").strip()
                start, end = _seconds(item.start_offset), _seconds(item.end_offset)
                if not text or start is None or end is None or end < start:
                    continue
                if duration is not None and start >= duration:
                    continue  # timestamp beyond this chunk
                words.append(Word(text, round(start + offset, 3), round(end + offset, 3)))
    return words


# ---- Gemini calls ----------------------------------------------------------------

def _retryable(exc: Exception) -> bool:
    return getattr(exc, "code", None) in (429, 500, 503, 504)


def _with_retries(call, label: str):
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            return call()
        except Exception as exc:
            if delay is None or not _retryable(exc):
                raise
            ui.warn(f"{label}: error {getattr(exc, 'code', '?')}, retrying in {delay}s "
                    f"({attempt + 1}/{len(RETRY_DELAYS)})")
            time.sleep(delay)


def _make_chunk(info: ffmpeg.MediaInfo, start: float, length: float, path: Path) -> None:
    """Small mono 16 kHz MP3 for upload only. Never used for the output."""
    ffmpeg.run(["-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(info.path),
                "-map", f"0:{info.audio_index}", "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "libmp3lame", "-b:a", "64k", str(path)], what="preparing audio for Gemini")


def _transcribe_chunk(client, chunk: Path, offset: float, length: float, label: str) -> list[Word]:
    from google.genai import types

    uploaded = client.files.upload(file=str(chunk))
    try:
        response = _with_retries(lambda: client.models.generate_content(
            model=model_name(),
            contents=[types.Content(role="user", parts=[
                types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type or "audio/mp3"),
            ])],
            config=types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(
                    mode="VERBATIM", word_timestamp=True),
            ),
        ), label)
        return extract_words(response, offset, length)
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


def transcribe(info: ffmpeg.MediaInfo, api_key: str, workers: int = MAX_WORKERS) -> list[Word]:
    """Transcribe the whole file in parallel chunks. Uses the cache when possible."""
    cached = load_cached(info)
    if cached is not None:
        ui.info(ui.dim(f"Using saved transcript ({len(cached)} words) - no Gemini call needed."))
        return cached

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version="v1beta"))
    count = max(1, math.ceil(info.duration / CHUNK_SECONDS))
    spans = [(i * CHUNK_SECONDS, min(CHUNK_SECONDS, info.duration - i * CHUNK_SECONDS)) for i in range(count)]
    spans = [s for s in spans if s[1] > 0.5] or [(0.0, info.duration)]
    ui.info(f"Transcribing with {model_name()} ({len(spans)} part{'s' if len(spans) > 1 else ''})...")

    results: dict[int, list[Word]] = {}
    failures: list[tuple[int, Exception]] = []
    with tempfile.TemporaryDirectory(prefix="kvm-") as tmp:
        def work(index: int, start: float, length: float) -> list[Word]:
            chunk = Path(tmp) / f"part{index:03d}.mp3"
            _make_chunk(info, start, length, chunk)
            return _transcribe_chunk(client, chunk, start, length, f"part {index + 1}/{len(spans)}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, len(spans))) as pool:
            futures = {pool.submit(work, i, s, n): i for i, (s, n) in enumerate(spans)}
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    results[i] = future.result()
                    ui.info(ui.dim(f"  part {i + 1}/{len(spans)} done ({len(results[i])} words)"))
                except Exception as exc:
                    failures.append((i, exc))
                    results[i] = []

    if len(failures) == len(spans):
        raise TranscribeError(f"Transcription failed for every part. First error: {failures[0][1]}")
    words = [w for i in range(len(spans)) for w in results[i]]
    if failures:
        for i, exc in sorted(failures, key=lambda f: f[0]):
            s, n = spans[i]
            ui.warn(f"Part {i + 1} ({s:.0f}-{s + n:.0f}s) was NOT transcribed, so it is not cleaned: {exc}")
    else:
        save_cached(info, words)
    if not words:
        raise TranscribeError("Gemini returned no words. Is there speech in this file?")
    return words

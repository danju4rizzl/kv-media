"""Line word cuts up with the real audio. Local and free (FFmpeg only).

Gemini's word times are coarse (a 0.1 s grid, some zero-length words), so a filler cut taken
straight from the transcript can leave the tail of an "mmm" or clip the next word. Here each
boundary of a word cut (filler / repeat / restart) moves to the quietest moment within
SEARCH_SECONDS of where Gemini put it, i.e. the gap between words. If there is no clear
quiet moment nearby (words run together), the boundary is left where it was.

Pause cuts are not touched: silencedetect already measured them on the audio.
"""

from __future__ import annotations

import math
import re

from kv_media import ffmpeg
from kv_media.cuts import MIN_CUT_SECONDS, Cut

FRAME_SECONDS = 0.01        # one loudness value per 10 ms
SEARCH_SECONDS = 0.15       # how far a boundary may move either way
MIN_DIP_DB = 6.0            # the quiet point must be this much quieter than the loudest nearby
NEAR_MIN_DB = 3.0           # frames this close to the quietest count as "quiet"; nearest one wins
FLOOR_DB = -100.0

_RMS = re.compile(r"RMS_level=(\S+)")
_cache: dict[tuple[str, int, int], list[float]] = {}


def parse_envelope(text: str) -> list[float]:
    levels = []
    for match in _RMS.finditer(text):
        try:
            value = float(match.group(1))
        except ValueError:
            value = FLOOR_DB
        levels.append(FLOOR_DB if math.isinf(value) or math.isnan(value) else max(FLOOR_DB, value))
    return levels


def envelope(info: ffmpeg.MediaInfo) -> list[float]:
    """Loudness (dB RMS) of every 10 ms of the source audio. Cached per file for re-plans."""
    stat = info.path.stat()
    key = (str(info.path.resolve()), stat.st_size, int(stat.st_mtime))
    if key not in _cache:
        samples = round(16000 * FRAME_SECONDS)
        proc = ffmpeg.run(
            ["-i", str(info.path), "-map", f"0:{info.audio_index}", "-vn", "-af",
             f"aformat=channel_layouts=mono,aresample=16000,asetnsamples=n={samples}:p=0,"
             "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=RMS_level,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"],
            what="measuring word gaps")
        _cache.clear()
        _cache[key] = parse_envelope(proc.stdout)
    return _cache[key]


def snap(time: float, levels: list[float], low: float, high: float) -> float:
    """Quietest moment near ``time`` (within [low, high]), or ``time`` if nothing is clearly quiet."""
    first = max(0, math.floor(max(low, time - SEARCH_SECONDS) / FRAME_SECONDS))
    last = min(len(levels) - 1, math.ceil(min(high, time + SEARCH_SECONDS) / FRAME_SECONDS))
    if last - first < 2:
        return time
    window = range(first, last + 1)
    quietest = min(levels[i] for i in window)
    loudest = max(levels[i] for i in window)
    if loudest - quietest < MIN_DIP_DB:
        return time
    quiet = [i for i in window if levels[i] <= quietest + NEAR_MIN_DB]
    # Use the middle of a frame (its centre is where the level was measured).
    best = min(quiet, key=lambda i: abs((i + 0.5) * FRAME_SECONDS - time))
    return round(min(high, max(low, (best + 0.5) * FRAME_SECONDS)), 3)


def align_cuts(cuts: list[Cut], levels: list[float]) -> tuple[list[Cut], int]:
    """Snap both edges of every word cut. Returns (cuts, how many moved)."""
    out, moved = [], 0
    for cut in cuts:
        middle = (cut.start + cut.end) / 2
        start = snap(cut.start, levels, 0.0, middle - MIN_CUT_SECONDS / 2)
        end = snap(cut.end, levels, middle + MIN_CUT_SECONDS / 2, float("inf"))
        if end - start < MIN_CUT_SECONDS:
            start, end = cut.start, cut.end
        if (start, end) != (cut.start, cut.end):
            moved += 1
        out.append(Cut(start, end, cut.reason, cut.text))
    return out, moved

"""Pause (dead air) detection with FFmpeg's silencedetect. Local and free."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kv_media import ffmpeg
from kv_media.cuts import MIN_CUT_SECONDS, Cut


@dataclass(frozen=True)
class PauseSettings:
    threshold_db: float = -35.0   # quieter than this counts as silence
    min_pause: float = 0.6        # only pauses at least this long are shortened
    keep: float = 0.2             # natural pause left on each side (protects word onsets/tails)

    def describe(self) -> str:
        return (f"quieter than {self.threshold_db:g} dB, longer than {self.min_pause:g} s, "
                f"keep {self.keep:g} s each side")


_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def parse_silencedetect(log: str, duration: float) -> list[tuple[float, float]]:
    """Silence intervals from silencedetect's log. A silence running to EOF is closed at ``duration``."""
    intervals: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in log.splitlines():
        end = _END.search(line)
        if end and open_start is not None:
            intervals.append((open_start, float(end.group(1))))
            open_start = None
            continue
        start = _START.search(line)
        if start:
            open_start = max(0.0, float(start.group(1)))
    if open_start is not None and open_start < duration:
        intervals.append((open_start, duration))
    return intervals


def silences_to_cuts(
    intervals: list[tuple[float, float]], duration: float, keep: float
) -> list[Cut]:
    """Shrink each silence by ``keep`` on the speech side(s). File edges need no lead-in/out."""
    cuts = []
    for start, end in intervals:
        cut_start = start if start <= 0.0 else start + keep
        cut_end = end if end >= duration else end - keep
        if cut_end - cut_start >= MIN_CUT_SECONDS:
            cuts.append(Cut(round(cut_start, 4), round(cut_end, 4), "pause"))
    return cuts


LONG_PAUSE_SECONDS = 10.0     # silences this long are often screen demos: user decides


def is_long(cut: Cut) -> bool:
    return cut.reason == "pause" and cut.duration >= LONG_PAUSE_SECONDS


def apply_long_mode(cuts: list[Cut], mode: str, keep_seconds: float) -> list[Cut]:
    """cut: remove long silences like any pause. keep: leave them alone.
    shorten: leave ``keep_seconds`` of each long silence (half each side), remove the rest."""
    if mode == "cut":
        return cuts
    out = []
    for cut in cuts:
        if not is_long(cut):
            out.append(cut)
        elif mode == "shorten":
            half = max(0.0, keep_seconds) / 2
            if cut.duration - 2 * half >= MIN_CUT_SECONDS:
                out.append(Cut(round(cut.start + half, 4), round(cut.end - half, 4), "pause"))
        elif mode != "keep":
            raise ValueError(f"Unknown long-pause mode '{mode}'.")
    return out


def find_silences(info: ffmpeg.MediaInfo, threshold_db: float, min_length: float) -> list[tuple[float, float]]:
    if not info.has_audio:
        raise ffmpeg.FFmpegError(f"{info.path.name} has no audio track, so pauses cannot be found.")
    proc = ffmpeg.run(
        ["-i", str(info.path), "-map", f"0:{info.audio_index}", "-vn",
         "-af", f"silencedetect=noise={threshold_db:g}dB:d={min_length:g}",
         "-f", "null", "-"],
        what="pause detection",
    )
    return parse_silencedetect(proc.stderr, info.duration)


def detect(info: ffmpeg.MediaInfo, settings: PauseSettings) -> list[Cut]:
    intervals = find_silences(info, settings.threshold_db, settings.min_pause)
    return silences_to_cuts(intervals, info.duration, settings.keep)


def longest_gap(info: ffmpeg.MediaInfo, threshold_db: float) -> float:
    """Longest quiet stretch at ``threshold_db`` (explains why nothing was found)."""
    intervals = find_silences(info, threshold_db, 0.05)
    return max((end - start for start, end in intervals), default=0.0)


def no_pauses_hint(info: ffmpeg.MediaInfo, settings: PauseSettings) -> str:
    gap = longest_gap(info, settings.threshold_db)
    looser = longest_gap(info, settings.threshold_db + 10)
    lines = [
        f"No pauses longer than {settings.min_pause:g} s were found.",
        f"Longest quiet stretch: {gap:.2f} s at {settings.threshold_db:g} dB, "
        f"{looser:.2f} s at {settings.threshold_db + 10:g} dB.",
    ]
    if looser < settings.min_pause:
        lines.append("This recording has almost no dead air (continuous speech, or background "
                     "noise/music under the voice). Filler-word removal (next feature) will help more.")
    else:
        lines.append(f"Try a higher threshold, e.g. {settings.threshold_db + 10:g} dB "
                     "(the room noise is louder than the current setting).")
    return "\n".join(lines)

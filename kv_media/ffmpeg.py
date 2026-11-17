"""Thin wrappers around the ffmpeg / ffprobe executables."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable


class FFmpegError(RuntimeError):
    """FFmpeg is missing or a command failed."""


_MAJOR: int | None = None


def major_version() -> int:
    """FFmpeg major version (0 if unknown). Cached."""
    global _MAJOR
    if _MAJOR is None:
        import re
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace").stdout
            match = re.search(r"ffmpeg version n?(\d+)\.", out)
            _MAJOR = int(match.group(1)) if match else 0
        except OSError:
            _MAJOR = 0
    return _MAJOR


def filter_script_args(path) -> list[str]:
    """Read a filtergraph from a file. FFmpeg 7+ uses -/filter_complex; older needs the script option."""
    if major_version() >= 7:
        return ["-/filter_complex", str(path)]
    return ["-filter_complex_script", str(path)]


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FFmpegError(
            f"{' and '.join(missing)} not found on PATH. Install FFmpeg "
            "(Windows: gyan.dev builds, macOS: brew install ffmpeg, Linux: apt install ffmpeg)."
        )


def run(args: list[str], what: str) -> subprocess.CompletedProcess:
    """Run ffmpeg with ``args``; raise FFmpegError with the tail of stderr on failure."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"FFmpeg failed during {what}:\n{tail}")
    return proc


def run_with_progress(
    args: list[str], what: str, total_seconds: float, on_progress: Callable[[float], None] | None
) -> None:
    """Run ffmpeg, reporting progress (0..1) from ``-progress`` output."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None and proc.stderr is not None
    for line in proc.stdout:
        if on_progress and line.startswith("out_time_us=") and total_seconds > 0:
            try:
                seconds = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                continue
            on_progress(max(0.0, min(1.0, seconds / total_seconds)))
    stderr = proc.stderr.read()
    if proc.wait() != 0:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"FFmpeg failed during {what}:\n{tail}")
    if on_progress:
        on_progress(1.0)


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    audio_index: int | None
    sample_rate: int | None
    channels: int | None
    audio_codec: str | None
    video_index: int | None
    fps: Fraction | None
    video_offset: float  # video start relative to the file's start (seconds)

    @property
    def has_video(self) -> bool:
        return self.video_index is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_index is not None


def _fraction(value: str | None) -> Fraction | None:
    try:
        frac = Fraction(value) if value else None
    except (ValueError, ZeroDivisionError):
        return None
    return frac if frac and frac > 0 else None


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_probe(path: Path, data: dict) -> MediaInfo:
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    duration = _float(fmt.get("duration"))
    if duration <= 0:
        duration = max((_float(s.get("duration")) for s in streams), default=0.0)

    fps = None
    video_offset = 0.0
    if video is not None:
        fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
        video_offset = max(0.0, _float(video.get("start_time")) - _float(fmt.get("start_time")))

    return MediaInfo(
        path=path,
        duration=duration,
        audio_index=audio["index"] if audio else None,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        channels=audio.get("channels") if audio else None,
        audio_codec=audio.get("codec_name") if audio else None,
        video_index=video["index"] if video else None,
        fps=fps,
        video_offset=video_offset,
    )


def probe(path: Path) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"Could not read {path.name}: {proc.stderr.strip() or 'ffprobe failed'}")
    return parse_probe(path, json.loads(proc.stdout or "{}"))


def enable_windows_ansi() -> None:
    if sys.platform == "win32":
        import os
        os.system("")  # turns on ANSI escape handling in the Windows console
        for stream in (sys.stdout, sys.stderr):
            try:  # transcripts may hold characters a legacy code page cannot print
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass

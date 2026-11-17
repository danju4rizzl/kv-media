"""Input path cleaning (paste / drag-and-drop) and output naming."""

from __future__ import annotations

import os
import re
from pathlib import Path

AUDIO_EXTS = {".wav", ".mp3", ".aac", ".flac", ".ogg", ".m4a", ".wma", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS
OUTPUT_DIR = "kvm_output"
SUFFIX = "_kvm"
VIDEO_OUT_EXTS = {".mp4", ".mkv", ".mov"}       # other containers are written as .mp4
AUDIO_OUT_EXTS = {".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus"}

_WINDOWS_PATH = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_POSIX_DRIVE = re.compile(r"^/(?:mnt/)?([A-Za-z])/(.*)$")


def _candidates(raw: str) -> list[str]:
    text = raw.strip()
    if text.startswith("& "):                      # PowerShell drag-and-drop: & 'C:\x.mp4'
        text = text[2:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    if text.startswith("file://"):
        text = text[7:]
    text = os.path.expanduser(text)

    options = [text]
    windows = _WINDOWS_PATH.match(text)
    if os.name == "nt":
        posix = _POSIX_DRIVE.match(text)             # Git Bash /c/... or WSL /mnt/c/... on Windows
        if posix:
            rest = re.sub(r"\\(.)", r"\1", posix[2])  # Git Bash escapes spaces: My\ Video.mp4
            options.append(f"{posix[1].upper()}:/{rest}")
    elif windows:                                   # Windows path pasted into WSL / Linux
        options.append(f"/mnt/{windows[1].lower()}/{windows[2].replace(chr(92), '/')}")
    elif "\\" in text:
        options.append(re.sub(r"\\(.)", r"\1", text))  # bash-escaped spaces: My\ Video.mp4
    return options


def clean_path(raw: str) -> Path:
    """Best interpretation of a typed, pasted or dragged path (the first that exists)."""
    options = [Path(o) for o in _candidates(raw)]
    return next((p for p in options if p.exists()), options[0])


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS


def list_media(folder: Path) -> list[Path]:
    """Media files in ``folder`` (not recursive), skipping earlier kvm results."""
    try:
        files = [p for p in folder.iterdir() if p.is_file() and is_media(p)]
    except OSError:
        return []
    return sorted((p for p in files if not p.stem.endswith(SUFFIX)), key=lambda p: p.name.lower())


def output_extension(source: Path, audio_only: bool, fmt: str | None) -> str:
    if fmt:
        return "." + fmt.lower().lstrip(".")
    ext = source.suffix.lower()
    if audio_only:
        return ext if ext in AUDIO_OUT_EXTS else (".wav" if ext in VIDEO_EXTS else ".m4a")
    return ext if ext in VIDEO_OUT_EXTS else ".mp4"


def output_path(source: Path, audio_only: bool, fmt: str | None, output: str | None) -> Path:
    """Default: ./kvm_output/<name>_kvm.<ext>. ``output`` may be a folder or a file."""
    ext = output_extension(source, audio_only, fmt)
    name = f"{source.stem}{SUFFIX}{ext}"
    if output:
        target = clean_path(output)
        if not target.is_absolute():
            target = Path.cwd() / target
        if output.endswith(("/", "\\")) or target.is_dir() or not target.suffix:
            return target / name
        return target
    return Path.cwd() / OUTPUT_DIR / name


def report_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_cuts.txt")

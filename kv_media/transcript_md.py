"""The transcript as Markdown: numbered, timestamped lines a person or an AI can read.

    [12] (00:51.3) so what we're going to [pause 1.4s] so today we set up the connectors

- One line per sentence (also broken at long pauses and after MAX_WORDS words).
- Line numbers are stable ids, so a reviewer (human or AI) can point at "line 12" and quote
  the words to remove; ``Line.first`` / ``Line.last`` map a line back to word timings.
- Verbatim: fillers, repeats and false starts are kept exactly as Gemini heard them.
- Times refer to the ORIGINAL recording. Writing this file is free (no Gemini call).
- Strike words out (~~like this~~) and run kvm again to cut them (edits.py). A transcript with
  strikes is never overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kv_media import report, transcribe
from kv_media.transcribe import Word

SENTENCE_END = (".", "?", "!", "…")
PAUSE_MARK_SECONDS = 1.0        # gaps at least this long are shown as [pause N.Ns]
LINE_BREAK_PAUSE = 2.0          # a gap this long also starts a new line
MAX_WORDS = 40


@dataclass(frozen=True)
class Line:
    number: int
    first: int          # index of the first word in the transcript
    last: int           # index of the last word (inclusive)
    start: float
    end: float


def split_lines(words: list[Word]) -> list[Line]:
    lines: list[Line] = []
    first = 0
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        ends = word.text.rstrip("\"')]”’").endswith(SENTENCE_END)
        long_gap = nxt is not None and nxt.start - word.end >= LINE_BREAK_PAUSE
        if nxt is None or ends or long_gap or i - first + 1 >= MAX_WORDS:
            lines.append(Line(len(lines) + 1, first, i, words[first].start, word.end))
            first = i + 1
    return lines


def line_text(words: list[Word], line: Line) -> str:
    parts = []
    for i in range(line.first, line.last + 1):
        if i > line.first:
            gap = words[i].start - words[i - 1].end
            if gap >= PAUSE_MARK_SECONDS:
                parts.append(f"[pause {gap:.1f}s]")
        parts.append(words[i].text)
    return " ".join(parts)


def _clock(seconds: float) -> str:
    return report.fmt_time(seconds)[:-2]          # 00:51.3 (one decimal is enough to find it)


def build(words: list[Word], source: Path, duration: float) -> str:
    lines = split_lines(words)
    out = [
        f"# Transcript: {source.stem}",
        "",
        f"- Source: `{source.name}`",
        f"- Length: {report.fmt_length(duration)}, {len(words)} words, {len(lines)} lines",
        f"- Transcribed by: {transcribe.model_name()} (verbatim: fillers, repeats and false starts kept)",
        "- Format: `[line] (start time) words`. Times refer to the ORIGINAL recording.",
        f"- `[pause N.Ns]` = silence of {PAUSE_MARK_SECONDS:g}s or more between words.",
        "",
        "---",
        "",
    ]
    previous_end = 0.0
    for line in lines:
        gap = line.start - previous_end
        if gap >= LINE_BREAK_PAUSE:
            out += [f"*[pause {gap:.1f}s]*", ""]
        out += [f"[{line.number}] ({_clock(line.start)}) {line_text(words, line)}", ""]
        previous_end = line.end
    if duration - previous_end >= LINE_BREAK_PAUSE:
        out += [f"*[pause {duration - previous_end:.1f}s to the end]*", ""]
    return "\n".join(out).rstrip() + "\n"


def path_for(output: Path) -> Path:
    """Next to an export: <name>_transcript.md."""
    return output.with_name(f"{output.stem}_transcript.md")


def is_edited(target: Path) -> bool:
    """True if ``target`` has words you struck out (~~ ~~): kvm never overwrites those."""
    from kv_media import edits
    try:
        return edits.has_strikes(target.read_text(encoding="utf-8-sig"))
    except OSError:
        return False


def write(words: list[Word], source: Path, duration: float, target: Path) -> Path:
    """Write the transcript, unless ``target`` already has your strikes (then it is left alone)."""
    if is_edited(target):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(words, source, duration), encoding="utf-8")
    return target

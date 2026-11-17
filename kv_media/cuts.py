"""The cut list: one model shared by every remover (pauses now; fillers and repeats later)."""

from __future__ import annotations

from dataclasses import dataclass

MIN_CUT_SECONDS = 0.05     # shorter than this is rounding noise (a quick "um" is ~0.1 s)
MIN_KEEP_SECONDS = 0.10    # kept slivers shorter than this between two cuts are absorbed
MAX_CUT_FRACTION = 0.80    # a plan removing more than this is treated as unreliable


class CutPlanError(ValueError):
    """The cut plan looks wrong (e.g. it would remove almost everything)."""


@dataclass(frozen=True)
class Cut:
    start: float
    end: float
    reason: str = "pause"
    text: str = ""          # words removed (empty for pauses)

    @property
    def duration(self) -> float:
        return self.end - self.start


def normalize(cuts: list[Cut], duration: float) -> list[Cut]:
    """Clamp, sort and merge cuts. Cuts closer together than MIN_KEEP_SECONDS are joined."""
    clamped = sorted(
        (Cut(max(0.0, c.start), min(duration, c.end), c.reason, c.text) for c in cuts),
        key=lambda c: c.start,
    )
    merged: list[Cut] = []
    for cut in clamped:
        if cut.duration <= 0:
            continue
        if merged and cut.start - merged[-1].end < MIN_KEEP_SECONDS:
            last = merged[-1]
            reasons = last.reason if cut.reason in last.reason.split("+") else f"{last.reason}+{cut.reason}"
            text = " ".join(t for t in (last.text, cut.text) if t)
            if cut.end <= last.end and cut.text and cut.text in last.text:
                text = last.text
            merged[-1] = Cut(last.start, max(last.end, cut.end), reasons, text)
        else:
            merged.append(cut)
    # A leading or trailing sliver is not worth keeping either.
    if merged and merged[0].start < MIN_KEEP_SECONDS:
        merged[0] = Cut(0.0, merged[0].end, merged[0].reason, merged[0].text)
    if merged and duration - merged[-1].end < MIN_KEEP_SECONDS:
        merged[-1] = Cut(merged[-1].start, duration, merged[-1].reason, merged[-1].text)
    return [c for c in merged if c.duration >= MIN_CUT_SECONDS]


def keep_segments(cuts: list[Cut], duration: float) -> list[tuple[float, float]]:
    """The (start, end) ranges that survive, in order. ``cuts`` must be normalized."""
    keeps: list[tuple[float, float]] = []
    position = 0.0
    for cut in cuts:
        if cut.start > position:
            keeps.append((position, cut.start))
        position = max(position, cut.end)
    if position < duration:
        keeps.append((position, duration))
    return keeps


def removed_seconds(cuts: list[Cut]) -> float:
    return sum(c.duration for c in cuts)


def check_plan(cuts: list[Cut], duration: float) -> None:
    if duration <= 0:
        raise CutPlanError("The file has no measurable duration.")
    fraction = removed_seconds(cuts) / duration
    if fraction > MAX_CUT_FRACTION:
        raise CutPlanError(
            f"This would remove {fraction:.0%} of the file, which looks wrong. "
            "The pause threshold is probably too high for this recording; try a lower one "
            "(e.g. --silence-threshold -45)."
        )

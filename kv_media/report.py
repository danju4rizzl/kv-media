"""Human-readable cut preview and the <name>_kvm_cuts.txt report."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kv_media.pipeline import Plan


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


def fmt_length(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"


def breakdown(plan: "Plan") -> str:
    counts: dict[str, int] = {}
    for cut in plan.cuts:
        for reason in cut.reason.split("+"):
            counts[reason] = counts.get(reason, 0) + 1
    labels = {"pause": "pauses", "filler": "fillers", "repeat": "repeats", "restart": "restarts"}
    return ", ".join(f"{n} {labels.get(r, r)}" for r, n in counts.items())


def summary_lines(plan: "Plan") -> list[str]:
    pct = plan.removed / plan.original_duration if plan.original_duration else 0.0
    lines = [
        f"Original length : {fmt_length(plan.original_duration)}",
        f"New length      : {fmt_length(plan.new_duration)}",
        f"Removed         : {fmt_length(plan.removed)} ({pct:.1%}) in {len(plan.cuts)} cuts",
    ]
    if plan.cuts:
        lines.append(f"Breakdown       : {breakdown(plan)}")
    return lines


def cut_table(plan: "Plan", limit: int | None = None) -> list[str]:
    rows = [f"{'#':>4}  {'start':>10}  {'length':>7}  {'reason':<14}  removed"]
    shown = plan.cuts if limit is None else plan.cuts[:limit]
    for i, cut in enumerate(shown, 1):
        text = cut.text if len(cut.text) <= 60 else cut.text[:57] + "..."
        removed = f'"{text}"' if text else "(silence)"
        rows.append(f"{i:>4}  {fmt_time(cut.start):>10}  {cut.duration:>6.2f}s  {cut.reason:<14}  {removed}")
    if limit is not None and len(plan.cuts) > limit:
        rows.append(f"      ... and {len(plan.cuts) - limit} more")
    return rows


def build_report(plan: "Plan") -> str:
    job = plan.job
    lines = [
        "kvm cut report",
        "==============",
        f"Source : {job.source}",
        f"Output : {plan.output}",
    ]
    if job.remove_pauses:
        lines.append(f"Pauses : {job.pause_settings.describe()}")
    if job.needs_words:
        lines.append(f"Words  : {', '.join(n for n in job.removal_names() if n != 'pauses')} "
                     f"(transcript: {len(plan.words)} words)")
    lines += ["", *summary_lines(plan), "",
              "Times refer to the ORIGINAL recording.", "", *cut_table(plan), ""]
    return "\n".join(lines)

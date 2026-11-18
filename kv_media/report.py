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
    labels = {"pause": "pauses", "filler": "fillers", "repeat": "repeats", "restart": "restarts",
              "ai-nonsense": "AI: nonsense", "ai-false-start": "AI: false starts",
              "ai-repeat": "AI: repeats", "ai-filler": "AI: fillers", "edit": "your edits"}
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
    if plan.word_cuts and plan.job.align_words:
        lines.append(f"Word cuts       : {plan.aligned} of {plan.word_cuts} moved to the quiet gap between words")
    if plan.ai_matches:
        approved = sum(1 for m in plan.ai_matches if plan.job.ai_is_approved(m))
        lines.append(f"AI suggestions  : {approved} of {len(plan.ai_matches)} approved")
    result = getattr(plan, "edit_result", None)
    if result is not None:
        lines.append(f"Your edits      : {result.struck_words} struck word(s) on {result.lines} line(s) "
                     f"from {plan.job.edits.name}")
        for missing in result.not_found:
            lines.append(f"  not found     : {missing}")
    if plan.kept:
        lines.append(f"Kept by you     : {plan.kept} cut{'s' if plan.kept != 1 else ''} left in")
    if plan.long_found:
        mode = {"cut": "cut", "shorten": f"shortened to {plan.job.long_keep:g}s", "keep": "kept"}[plan.job.long_pauses]
        lines.append(f"Long silences   : {len(plan.long_found)} of 10s+, {fmt_length(sum(c.duration for c in plan.long_found))} "
                     f"in total ({mode})")
    return lines


def ai_table(plan: "Plan") -> list[str]:
    rows = [f"{'#':>4}  {'ok':<3} {'start':>10}  {'conf':>4}  {'reason':<12} remove"]
    for i, m in enumerate(plan.ai_matches, 1):
        s = m.suggestion
        text = s.remove if len(s.remove) <= 50 else s.remove[:47] + "..."
        mark = "yes" if plan.job.ai_is_approved(m) else "-"
        why = f"  ({s.why})" if s.why else ""
        rows.append(f"{i:>4}  {mark:<3} {fmt_time(m.start):>10}  {s.confidence:>4.2f}  {s.reason:<12} "
                    f'"{text}"{why}')
    return rows


def longest_pauses(plan: "Plan", limit: int = 5, minimum: float = 2.0) -> list[str]:
    """The biggest silences (where the dead air is), longest first."""
    def overlaps_long(cut) -> bool:
        return any(cut.start < big.end and big.start < cut.end for big in plan.long_found)

    others = [c for c in plan.cuts if "pause" in c.reason.split("+") and not overlaps_long(c)]
    pauses = sorted(plan.long_found + others, key=lambda c: c.duration, reverse=True)
    return [f"{fmt_length(c.duration):>9} at {fmt_time(c.start)}"
            for c in pauses if c.duration >= minimum][:limit]


def timeline(plan: "Plan", width: int = 60) -> list[str]:
    """One text line showing where cuts fall: '=' kept, '+' partly cut, '#' mostly cut."""
    total = plan.original_duration
    if total <= 0 or not plan.cuts:
        return []
    step = total / width
    removed = [0.0] * width
    for cut in plan.cuts:
        first, last = int(cut.start / step), min(width - 1, int(cut.end / step))
        for i in range(first, last + 1):
            lo, hi = i * step, (i + 1) * step
            removed[i] += max(0.0, min(hi, cut.end) - max(lo, cut.start))
    bar = "".join("#" if r / step >= 0.5 else "+" if r / step >= 0.05 else "=" for r in removed)
    return [f"{fmt_length(0)} |{bar}| {fmt_length(total)}",
            f"{'':>{len(fmt_length(0))}}  '=' kept  '+' some cut  '#' mostly cut  (1 char = {fmt_length(step)})"]


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
    if plan.title:
        lines.append(f"Title  : {plan.title}")
    if job.remove_pauses:
        lines.append(f"Pauses : {job.pause_settings.describe()}")
    if job.needs_words:
        lines.append(f"Words  : {', '.join(n for n in job.removal_names() if n != 'pauses')} "
                     f"(transcript: {len(plan.words)} words)")
    lines += ["", *summary_lines(plan), ""]
    longest = longest_pauses(plan)
    if longest:
        lines += ["Longest pauses:", *longest, ""]
    lines += [*timeline(plan), "", "Times refer to the ORIGINAL recording.", "", *cut_table(plan), ""]
    if plan.ai_matches:
        lines += ["AI suggestions (yes = cut):", *ai_table(plan), ""]
    return "\n".join(lines)

"""Interactive mode: run `kvm` with no task flags."""

from __future__ import annotations

from pathlib import Path

from kv_media import disfluency, keys, paths, pauses, pipeline, report, titles, ui
from kv_media.pauses import PauseSettings

AUDIO_FORMATS = [
    ("1", "WAV   - lossless, largest"),
    ("2", "FLAC  - lossless, smaller"),
    ("3", "MP3   - 320 kbps"),
    ("4", "M4A   - AAC 256 kbps"),
]
AUDIO_FORMAT_EXT = {"1": "wav", "2": "flac", "3": "mp3", "4": "m4a"}
REMOVALS = [
    ("1", "Pauses / dead air", "free, on this computer"),
    ("2", "Filler words (um, ummm, ah, ahhh, ...)", "uses Gemini"),
    ("3", "Repeated words / phrases and restarted sentences", "uses Gemini"),
    ("4", "AI review: fillers the list misses, stumbles, false starts, words that make no sense",
     "cheap Gemini text call, you approve; on automatically with 2 or 3"),
]


def _list_files(files: list[Path]) -> None:
    for i, f in enumerate(files[:30], 1):
        print(f"    {ui.bold(str(i))}) {f.name}")
    if len(files) > 30:
        ui.info(ui.dim(f"... {len(files) - 30} more not shown; paste a path to pick one of them"))


def ask_source(initial: Path | None) -> Path:
    ui.step(1, "Choose a file")
    if initial is not None:
        try:
            pipeline.validate_source(initial)
            ui.info(f"Using {ui.bold(initial.name)}")
            return initial
        except pipeline.JobError as exc:
            ui.warn(str(exc))

    folder = Path.cwd()
    while True:
        files = paths.list_media(folder)
        if files:
            ui.info(f"Media files in {folder}:")
            _list_files(files)
            raw = ui.ask("Number, or paste / drag a file or folder path")
        else:
            ui.info(f"No media files in {folder}.")
            raw = ui.ask("Paste or drag a file or folder path")
        if not raw:
            continue
        if raw.isdigit() and files and 1 <= int(raw) <= min(len(files), 30):
            return files[int(raw) - 1]
        candidate = paths.clean_path(raw)
        if candidate.is_dir():
            folder = candidate
            continue
        try:
            pipeline.validate_source(candidate)
            return candidate
        except pipeline.JobError as exc:
            ui.warn(str(exc))


def ask_task(source: Path) -> tuple[bool, str | None]:
    """Returns (audio_only, format)."""
    ui.step(2, "Task")
    if source.suffix.lower() in paths.AUDIO_EXTS:
        ui.info("Audio file - working on the audio.")
        return True, None
    choice = ui.choose("Choose", [("1", "Video  - edit the video (picture + sound)"),
                                  ("2", "Audio only - export just the edited sound")], "1")
    if choice == "1":
        return False, None
    ui.info("Audio format:")
    fmt = ui.choose("Choose", AUDIO_FORMATS, "1")
    return True, AUDIO_FORMAT_EXT[fmt]


def ask_edits(job: pipeline.Job) -> None:
    """If you struck words out in this recording's transcript (~~ ~~), offer to cut them."""
    found = pipeline.find_edits(job)
    if found is None:
        return
    ui.info(f"Found your transcript edits: {found}")
    ui.info(ui.dim("Words you struck out (~~like this~~) will be cut, on top of what you choose next."))
    if ui.ask("Cut the words you struck out? (y/n)", "y").lower().startswith("y"):
        job.edits = found


def default_removals() -> str:
    """First-time default: every option (Enter = remove everything), derived from REMOVALS so it
    follows when options are added or removed. The AI review is left out if KVM_AI_REVIEW=off."""
    keys_ = [k for k, _, _ in REMOVALS if not (k == "4" and not pipeline.ai_review_auto())]
    return ",".join(keys_)


def ask_removals(job: pipeline.Job) -> None:
    """Multi-select: sets remove_pauses / remove_fillers / remove_repeats on ``job``."""
    ui.step(3, "What should be removed?")
    for key, label, note in REMOVALS:
        print(f"    {ui.bold(key)}) {label}  {ui.dim('- ' + note)}")
    ui.info(ui.dim(f"Filler list: {disfluency.fillers_source(job.words_file)} (edit it to add words)"))
    current = ",".join(k for k, on in (("1", job.remove_pauses), ("2", job.remove_fillers),
                                       ("3", job.remove_repeats), ("4", job.ai_review)) if on) or default_removals()
    while True:
        raw = ui.ask("Choose one or more (e.g. 1,2,3,4)", current)
        picks = {p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()}
        if picks and picks <= {"1", "2", "3", "4"}:
            job.remove_pauses, job.remove_fillers, job.remove_repeats, job.ai_review = (
                "1" in picks, "2" in picks, "3" in picks, "4" in picks)
            auto = job.apply_auto_ai_review()
            ui.info("Removing: " + ", ".join(job.removal_names()))
            if auto:
                ui.info(ui.dim("AI review added automatically: it reads the transcript for fillers and "
                               "stumbles the word list misses (set KVM_AI_REVIEW=off to stop this)."))
            return
        ui.warn("Please enter numbers from 1 to 4, separated by commas.")


def _number(prompt: str, default: float, low: float, high: float) -> float:
    while True:
        raw = ui.ask(prompt, f"{default:g}")
        try:
            value = float(raw)
        except ValueError:
            ui.warn("Please enter a number.")
            continue
        if low <= value <= high:
            return value
        ui.warn(f"Please enter a value between {low:g} and {high:g}.")


def ask_pause_settings(current: PauseSettings) -> PauseSettings:
    ui.info(ui.dim("Higher threshold (e.g. -30) = more counts as a pause (noisy rooms). "
                   "Lower (e.g. -45) = only very quiet gaps."))
    threshold = _number("Silence threshold in dB", current.threshold_db, -80, -10)
    min_pause = _number("Shorten pauses longer than (seconds)", current.min_pause, 0.2, 10)
    keep = _number("Natural pause to keep each side (seconds)", current.keep, 0.0, 2)
    return PauseSettings(threshold, min_pause, keep)


def show_preview(plan: pipeline.Plan, show_all: bool = False) -> None:
    ui.step(4, "Preview")
    if plan.job.remove_pauses:
        ui.info(f"Pauses: {plan.job.pause_settings.describe()}")
    for line in report.summary_lines(plan):
        ui.info(line)
    if plan.cuts:
        print()
        for line in report.timeline(plan):
            ui.info(line)
    longest = report.longest_pauses(plan)
    if longest:
        print()
        ui.info(ui.bold("Longest pauses") + ui.dim(" (where the dead air is)"))
        for line in longest:
            ui.info(line)
        if plan.long_found and plan.job.long_pauses == "cut":
            ui.info(ui.dim("Silences of 10s+ may be screen demos. Press l to shorten or keep them."))
    if plan.cuts:
        print()
        for line in report.cut_table(plan, None if show_all else 15):
            ui.info(line)
    ui.info(f"Output: {plan.output}")


def parse_numbers(raw: str, count: int) -> set[int] | None:
    """'3, 5-7' -> {3, 5, 6, 7}; None if anything is out of range or not a number."""
    picks: set[int] = set()
    for part in raw.replace(" ", ",").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        if not lo.isdigit() or (hi and not hi.isdigit()):
            return None
        a, b = int(lo), int(hi or lo)
        if not 1 <= a <= b <= count:
            return None
        picks.update(range(a, b + 1))
    return picks or None


def ask_keep_cuts(plan: pipeline.Plan) -> None:
    if len(plan.cuts) > 15:
        for line in report.cut_table(plan):
            ui.info(line)
    ui.info(ui.dim("These cuts will NOT be removed."))
    while True:
        raw = ui.ask("Cut numbers to keep, e.g. 3,5-7 (Enter = none)")
        if not raw:
            return
        picks = parse_numbers(raw, len(plan.cuts))
        if picks:
            plan.job.kept_cuts |= {pipeline.cut_key(plan.cuts[i - 1]) for i in picks}
            return
        ui.warn(f"Please enter numbers from 1 to {len(plan.cuts)}, e.g. 3,5-7.")


def ask_ai_approval(plan: pipeline.Plan) -> None:
    """Show every AI suggestion; the user decides which become cuts."""
    job = plan.job
    ui.info(ui.bold("AI suggestions") + ui.dim(f" ({len(plan.ai_matches)}; 'yes' = will be cut)"))
    for line in report.ai_table(plan):
        ui.info(line)
    ui.info(ui.dim("Words that look odd may be Gemini mishearing you; only approve what you really said wrong."))
    high = [i for i, m in enumerate(plan.ai_matches, 1) if m.suggestion.confidence >= job.ai_min_confidence]
    ui.info("Which suggestions should be cut?")
    ui.info(ui.dim(f"  Enter = the {len(high)} marked 'yes' (AI at least {job.ai_min_confidence:g} sure)   "
                   "all = every one   none = cut nothing   1,3-5 = only those numbers"))
    ui.info(ui.dim("  You still see every cut in the preview next, and can keep any of them with 'k'."))
    while True:
        raw = ui.ask("Cut which", "").lower()
        if raw in ("", "yes", "y"):
            picks = set(high)
        elif raw == "all":
            picks = set(range(1, len(plan.ai_matches) + 1))
        elif raw == "none":
            picks = set()
        else:
            picks = parse_numbers(raw, len(plan.ai_matches))
            if picks is None:
                ui.warn(f"Please enter numbers from 1 to {len(plan.ai_matches)}, 'all' or 'none'.")
                continue
        job.ai_approved = {plan.ai_matches[i - 1].key for i in picks}
        ui.info(f"{len(picks)} AI suggestion(s) approved.")
        return


def ask_long_pauses(job: pipeline.Job, plan: pipeline.Plan) -> None:
    total = report.fmt_length(sum(c.duration for c in plan.long_found))
    ui.info(f"{len(plan.long_found)} silence(s) of 10s or more, {total} in total.")
    ui.info(ui.dim("In a screen recording these are often you showing something without talking."))
    choice = ui.choose("Long silences", [("1", "Cut them like any pause"),
                                         ("2", "Shorten each to a few seconds"),
                                         ("3", "Keep them (no change)")],
                       {"cut": "1", "shorten": "2", "keep": "3"}[job.long_pauses])
    job.long_pauses = {"1": "cut", "2": "shorten", "3": "keep"}[choice]
    if job.long_pauses == "shorten":
        job.long_keep = _number("Seconds of silence to leave", job.long_keep, 0.5, 9)


def _explain_nothing_found(plan: pipeline.Plan) -> None:
    job = plan.job
    if job.remove_pauses:
        ui.info(ui.dim("Measuring the quiet parts..."))
        for line in pauses.no_pauses_hint(plan.info, job.pause_settings).splitlines():
            ui.warn(line)
    if job.needs_words:
        ui.warn(f"No {' or '.join(n for n in job.removal_names() if n != 'pauses')} found "
                f"in {len(plan.words)} transcribed words.")


def show_titles(pack: titles.TitlePack) -> None:
    for i, t in enumerate(pack.titles, 1):
        size = len(t.text)
        note = ui.dim(f"{t.style}, {size} chars") if size <= 60 else ui.yellow(f"{t.style}, {size} chars - long")
        print(f"    {ui.bold(str(i))}) {t.text}  {note}")
    if pack.keyword:
        ui.info(ui.dim(f"Primary keyword: {pack.keyword}"))
    if pack.model:
        ui.info(ui.dim(f"Written by: {pack.model}"))


def ask_title(plan: pipeline.Plan) -> None:
    """Step 5: optional AI title. Sets plan.title / plan.seo and renames plan.output."""
    ui.step(5, "Title (AI, optional)")
    if plan.words:
        ui.info(ui.dim("Gemini reads the transcript and suggests SEO-friendly titles with different hooks."))
    else:
        ui.info(ui.dim("Gemini transcribes the file once (saved for next time), then suggests titles."))
    ui.info(ui.dim("You also get a description, tags and hashtags in a _seo.txt file."))
    if not ui.ask("Generate an AI title? (y/n)", "y").lower().startswith("y"):
        ui.info(f"Keeping the name {ui.bold(plan.output.name)}")
        return

    rejected: list[str] = []
    while True:
        ui.info(ui.dim("Writing titles..."))
        try:
            pack = pipeline.suggest_titles(plan, keys.ask_for_key, rejected or None)
        except pipeline.JobError as exc:
            ui.warn(str(exc))
            if ui.choose("Choose", [("r", "Try again"), ("k", "Keep the original name")], "k") == "r":
                continue
            return
        show_titles(pack)
        print(f"    {ui.bold('r')}) New ideas   {ui.bold('w')}) Write my own   "
              f"{ui.bold('k')}) Keep {plan.output.name}")
        numbers = [str(i) for i in range(1, len(pack.titles) + 1)]
        choice = ui.choose_quiet("Choose", [*numbers, "r", "w", "k"], "1")
        if choice == "k":
            ui.info(f"Keeping the name {ui.bold(plan.output.name)}")
            return
        if choice == "r":
            rejected += [t.text for t in pack.titles]
            continue
        default = "" if choice == "w" else pack.titles[int(choice) - 1].text
        title = titles.clean_title(ui.ask("Title (Enter keeps it, or type to edit)", default))
        if not title:
            ui.warn("The title is empty.")
            continue
        if len(title) > 60:
            ui.warn(f"{len(title)} characters: may be cut off in search results (60 or fewer is best).")
        pipeline.apply_title(plan, title, pack)
        ui.info(f"Title : {ui.bold(plan.title)}")
        if plan.output.parent != plan.output.parent.parent:
            ui.info(f"Folder: {plan.output.parent}")
        ui.info(f"File  : {plan.output.name}")
        return


def run(initial: Path | None = None, base: pipeline.Job | None = None) -> pipeline.Plan | None:
    """Walk the steps. Returns the confirmed plan, or None if the user quit."""
    print(ui.bold("kvm") + ui.dim(" - remove the unimportant parts of a recording"))
    source = ask_source(initial)
    audio_only, fmt = ask_task(source)
    job = pipeline.Job(
        source=source, audio_only=audio_only, fmt=fmt,
        remove_pauses=False,
        output=base.output if base else None,
        words_file=base.words_file if base else None,
        pause_settings=base.pause_settings if base else PauseSettings(),
        align_words=base.align_words if base else True,
        long_pauses=base.long_pauses if base else "cut",
        long_keep=base.long_keep if base else 2.0,
        ai_min_confidence=base.ai_min_confidence if base else 0.8,
    )
    ask_edits(job)
    ask_removals(job)

    show_all = False
    while True:
        ui.info(ui.dim("Finding what to remove..."))
        try:
            plan = pipeline.make_plan(job, ask_key=keys.ask_for_key)
        except pipeline.JobError as exc:
            ui.warn(str(exc))
            choice = ui.choose("Choose", [("c", "Change what to remove"),
                                          ("s", "Change pause sensitivity"), ("q", "Quit")], "c")
            if choice == "q":
                return None
            if choice == "c":
                ask_removals(job)
            else:
                job.pause_settings = ask_pause_settings(job.pause_settings)
            continue

        if job.ai_review and plan.ai_note:
            ui.warn(plan.ai_note)
        if plan.ai_matches and job.ai_approved is None:
            ask_ai_approval(plan)
            continue                                  # re-plan with the approved suggestions
        show_preview(plan, show_all)
        options = []
        if plan.cuts or job.audio_only:
            options.append(("e", "Export" if plan.cuts else "Export the audio anyway (no cuts)"))
        if plan.cuts:
            options.append(("a", "Show all cuts"))
            options.append(("k", "Keep some cuts (leave them in)"))
        else:
            _explain_nothing_found(plan)
        if job.kept_cuts:
            options.append(("u", "Undo kept cuts (remove them again)"))
        if plan.long_found:
            options.append(("l", "Long silences: cut, shorten or keep"))
        if plan.ai_matches:
            options.append(("i", "Change which AI suggestions are cut"))
        options.append(("c", "Change what to remove"))
        if job.remove_pauses:
            options.append(("s", "Change pause sensitivity"))
        options.append(("q", "Quit"))
        choice = ui.choose("Choose", options, "e" if plan.cuts else "c")
        if choice == "e":
            ask_title(plan)
            return plan
        if choice == "q":
            return None
        show_all = choice == "a"
        if choice == "k":
            ask_keep_cuts(plan)
        elif choice == "u":
            job.kept_cuts.clear()
        elif choice == "l":
            ask_long_pauses(job, plan)
        elif choice == "i":
            ask_ai_approval(plan)
        elif choice == "c":
            ask_removals(job)
        elif choice == "s":
            job.pause_settings = ask_pause_settings(job.pause_settings)

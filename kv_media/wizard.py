"""Interactive mode: run `kvm` with no task flags."""

from __future__ import annotations

from pathlib import Path

from kv_media import disfluency, keys, paths, pauses, pipeline, report, ui
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


def ask_removals(job: pipeline.Job) -> None:
    """Multi-select: sets remove_pauses / remove_fillers / remove_repeats on ``job``."""
    ui.step(3, "What should be removed?")
    for key, label, note in REMOVALS:
        print(f"    {ui.bold(key)}) {label}  {ui.dim('- ' + note)}")
    ui.info(ui.dim(f"Filler list: {disfluency.fillers_source(job.words_file)} (edit it to add words)"))
    current = ",".join(k for k, on in (("1", job.remove_pauses), ("2", job.remove_fillers),
                                       ("3", job.remove_repeats)) if on) or "1,2,3"
    while True:
        raw = ui.ask("Choose one or more (e.g. 1,2,3)", current)
        picks = {p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()}
        if picks and picks <= {"1", "2", "3"}:
            job.remove_pauses, job.remove_fillers, job.remove_repeats = ("1" in picks, "2" in picks, "3" in picks)
            ui.info("Removing: " + ", ".join(job.removal_names()))
            return
        ui.warn("Please enter numbers from 1 to 3, separated by commas.")


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
        for line in report.cut_table(plan, None if show_all else 15):
            ui.info(line)
    ui.info(f"Output: {plan.output}")


def _explain_nothing_found(plan: pipeline.Plan) -> None:
    job = plan.job
    if job.remove_pauses:
        ui.info(ui.dim("Measuring the quiet parts..."))
        for line in pauses.no_pauses_hint(plan.info, job.pause_settings).splitlines():
            ui.warn(line)
    if job.needs_words:
        ui.warn(f"No {' or '.join(n for n in job.removal_names() if n != 'pauses')} found "
                f"in {len(plan.words)} transcribed words.")


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
    )
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

        show_preview(plan, show_all)
        options = []
        if plan.cuts or job.audio_only:
            options.append(("e", "Export" if plan.cuts else "Export the audio anyway (no cuts)"))
        if plan.cuts:
            options.append(("a", "Show all cuts"))
        else:
            _explain_nothing_found(plan)
        options.append(("c", "Change what to remove"))
        if job.remove_pauses:
            options.append(("s", "Change pause sensitivity"))
        options.append(("q", "Quit"))
        choice = ui.choose("Choose", options, "e" if plan.cuts else "c")
        if choice == "e":
            return plan
        if choice == "q":
            return None
        show_all = choice == "a"
        if choice == "c":
            ask_removals(job)
        elif choice == "s":
            job.pause_settings = ask_pause_settings(job.pause_settings)

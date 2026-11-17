"""A job: what to do to one file. Plan first (detect + preview), then export."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kv_media import cuts as cutlist
from kv_media import disfluency, ffmpeg, keys, pauses, paths, render, report, transcribe


class JobError(RuntimeError):
    """A problem with the job the user can fix (bad path, no audio, ...)."""


@dataclass
class Job:
    source: Path
    audio_only: bool = False
    remove_pauses: bool = True
    remove_fillers: bool = False
    remove_repeats: bool = False
    words_file: Path | None = None
    pause_settings: pauses.PauseSettings = field(default_factory=pauses.PauseSettings)
    fmt: str | None = None
    output: str | None = None

    @property
    def needs_words(self) -> bool:
        return self.remove_fillers or self.remove_repeats

    def removal_names(self) -> list[str]:
        names = [n for n, on in (("pauses", self.remove_pauses), ("filler words", self.remove_fillers),
                                 ("repeats", self.remove_repeats)) if on]
        return names


@dataclass
class Plan:
    job: Job
    info: ffmpeg.MediaInfo
    cuts: list[cutlist.Cut]
    segments: list[render.Segment]
    output: Path
    words: list[transcribe.Word] = field(default_factory=list)

    @property
    def with_video(self) -> bool:
        return not self.job.audio_only and self.info.has_video

    @property
    def original_duration(self) -> float:
        return self.info.duration

    @property
    def new_duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def removed(self) -> float:
        return self.original_duration - self.new_duration

    @property
    def report_path(self) -> Path:
        return paths.report_path(self.output)


def validate_source(source: Path) -> None:
    if not source.exists():
        raise JobError(f"File not found: {source}")
    if source.is_dir():
        raise JobError(f"That is a folder, not a file: {source}")
    if not paths.is_media(source):
        raise JobError(f"Unsupported file type '{source.suffix}'. "
                       f"Supported: {', '.join(sorted(paths.MEDIA_EXTS))}")


def get_words(job: Job, info: ffmpeg.MediaInfo, ask_key: Callable[[], str | None] | None) -> list[transcribe.Word]:
    cached = transcribe.load_cached(info)
    if cached is not None:
        from kv_media import ui
        ui.info(ui.dim(f"Using saved transcript ({len(cached)} words) - no Gemini call needed."))
        return cached
    key = keys.find_key() or (ask_key() if ask_key else None)
    if not key:
        raise JobError("Filler and repeat removal need a Gemini API key. Set GEMINI_API_KEY "
                       f"(environment, a .env file, or {keys.CONFIG_ENV}) or run kvm to be asked.")
    try:
        return transcribe.transcribe(info, key)
    except transcribe.TranscribeError as exc:
        raise JobError(str(exc)) from exc
    except Exception as exc:  # API / network errors: show the message, never the key
        raise JobError(f"Gemini request failed: {exc}") from exc


def make_plan(job: Job, ask_key: Callable[[], str | None] | None = None) -> Plan:
    validate_source(job.source)
    ffmpeg.require_ffmpeg()
    info = ffmpeg.probe(job.source)
    if not info.has_audio:
        raise JobError(f"{job.source.name} has no audio track.")
    if not info.has_video:
        job.audio_only = True
    if job.fmt and paths.output_extension(job.source, True, job.fmt) not in paths.AUDIO_OUT_EXTS | paths.VIDEO_OUT_EXTS:
        raise JobError(f"Unsupported output format '{job.fmt}'.")

    found: list[cutlist.Cut] = []
    if job.remove_pauses:
        found += pauses.detect(info, job.pause_settings)
    words: list[transcribe.Word] = []
    if job.needs_words:
        words = get_words(job, info, ask_key)
        fillers = disfluency.load_fillers(job.words_file)
        if job.remove_fillers:
            found += disfluency.find_filler_cuts(words, fillers)
        if job.remove_repeats:
            found += disfluency.find_repeat_cuts(words, fillers)
            found += disfluency.find_restart_cuts(words, fillers)
    normalized = cutlist.normalize(found, info.duration)
    try:
        cutlist.check_plan(normalized, info.duration)
    except cutlist.CutPlanError as exc:
        raise JobError(str(exc)) from exc

    with_video = not job.audio_only and info.has_video
    keeps = cutlist.keep_segments(normalized, info.duration)
    segments = render.plan_segments(keeps, info, with_video)
    if not segments:
        raise JobError("Nothing would be left after cutting.")

    output = paths.output_path(job.source, job.audio_only, job.fmt, job.output)
    if output.resolve() == job.source.resolve():
        raise JobError("The output would overwrite the source file. Choose another output.")
    return Plan(job, info, normalized, segments, output, words)


def execute(plan: Plan, on_progress: Callable[[float], None] | None = None) -> None:
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    render.export(plan.info, plan.segments, plan.output, plan.with_video,
                  work_dir=plan.output.parent, on_progress=on_progress)
    if plan.cuts:
        plan.report_path.write_text(report.build_report(plan), encoding="utf-8")

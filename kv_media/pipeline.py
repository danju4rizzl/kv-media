"""A job: what to do to one file. Plan first (detect + preview), then export."""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kv_media import cuts as cutlist
from kv_media import (disfluency, edits, ffmpeg, keys, pauses, paths, refine, render, report, review, titles,
                      transcribe, transcript_md)


LONG_PAUSE_MODES = ("cut", "shorten", "keep")


def cut_key(cut: cutlist.Cut) -> tuple[float, float]:
    """Stable id of a cut across re-plans with the same settings (used to keep single cuts)."""
    return (round(cut.start, 2), round(cut.end, 2))


class JobError(RuntimeError):
    """A problem with the job the user can fix (bad path, no audio, ...)."""


def ai_review_auto() -> bool:
    """False when the user switched the automatic AI review off (KVM_AI_REVIEW=off/0/no/false,
    in the environment or a .env file)."""
    keys.load_env_files()
    return os.environ.get("KVM_AI_REVIEW", "").strip().lower() not in {"off", "0", "no", "false"}


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
    ai_title: bool = False
    align_words: bool = True              # snap word cuts to the quiet gap between words
    long_pauses: str = "cut"              # pauses >= pauses.LONG_PAUSE_SECONDS: cut | shorten | keep
    long_keep: float = 2.0                # seconds of silence left when shortening
    kept_cuts: set[tuple[float, float]] = field(default_factory=set)   # cuts the user chose to keep
    ai_review: bool = False               # AI reads the transcript and suggests cuts (cheap text model)
    ai_min_confidence: float = 0.8        # suggestions at/above this are pre-approved
    ai_approved: set[tuple[float, float]] | None = None   # None = use ai_min_confidence
    edits: Path | None = None             # a _transcript.md with ~~struck~~ words to cut

    @property
    def needs_words(self) -> bool:
        return self.remove_fillers or self.remove_repeats or self.ai_review or self.edits is not None

    def removal_names(self) -> list[str]:
        names = [n for n, on in (("pauses", self.remove_pauses), ("filler words", self.remove_fillers),
                                 ("repeats", self.remove_repeats), ("AI review", self.ai_review),
                                 ("your transcript edits", self.edits is not None)) if on]
        return names

    def apply_auto_ai_review(self) -> bool:
        """Turn the AI review on when fillers or repeats are removed (unless KVM_AI_REVIEW=off).

        The word list only knows um/uh/...; the AI review catches what it misses ("you know",
        stumbles, false starts) from the same transcript. Returns True when it was switched on here.
        """
        if self.ai_review or not (self.remove_fillers or self.remove_repeats) or not ai_review_auto():
            return False
        self.ai_review = True
        return True

    def ai_is_approved(self, match: review.Match) -> bool:
        if self.ai_approved is None:
            return match.suggestion.confidence >= self.ai_min_confidence
        return match.key in self.ai_approved


@dataclass
class Plan:
    job: Job
    info: ffmpeg.MediaInfo
    cuts: list[cutlist.Cut]
    segments: list[render.Segment]
    output: Path
    words: list[transcribe.Word] = field(default_factory=list)
    title: str | None = None
    seo: titles.TitlePack | None = None
    aligned: int = 0                       # word cuts moved to a quiet point
    word_cuts: int = 0
    kept: int = 0                          # cuts left out because the user kept them
    long_found: list[cutlist.Cut] = field(default_factory=list)   # long silences, before the mode
    ai_matches: list[review.Match] = field(default_factory=list)   # every AI suggestion found
    ai_note: str = ""                      # why there are no AI suggestions (error), if any
    edit_result: edits.EditResult | None = None   # words you struck in the transcript

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

    @property
    def transcript_path(self) -> Path:
        return transcript_md.path_for(self.output)

    @property
    def seo_path(self) -> Path:
        return titles.seo_path(self.output)


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
        raise JobError("Transcribing (fillers, repeats, AI titles, --transcript) needs a Gemini API key. Set GEMINI_API_KEY "
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
    long_found: list[cutlist.Cut] = []
    if job.remove_pauses:
        pause_cuts = pauses.detect(info, job.pause_settings)
        long_found = [c for c in pause_cuts if pauses.is_long(c)]
        found += pauses.apply_long_mode(pause_cuts, job.long_pauses, job.long_keep)
    words: list[transcribe.Word] = []
    word_cuts: list[cutlist.Cut] = []
    aligned = 0
    ai_matches: list[review.Match] = []
    ai_note = ""
    edit_result: edits.EditResult | None = None
    if job.needs_words:
        words = get_words(job, info, ask_key)
        fillers = disfluency.load_fillers(job.words_file)
        if job.remove_fillers:
            word_cuts += disfluency.find_filler_cuts(words, fillers)
        if job.remove_repeats:
            word_cuts += disfluency.find_repeat_cuts(words, fillers)
            word_cuts += disfluency.find_restart_cuts(words, fillers)
        if job.ai_review:
            ai_matches, ai_note = ai_suggestions(words, info.duration, ask_key)
            word_cuts += [m.cut() for m in ai_matches if job.ai_is_approved(m)]
        if job.edits is not None:
            try:
                edit_result = edits.load(job.edits, words)
            except edits.EditError as exc:
                raise JobError(str(exc)) from exc
            word_cuts += edit_result.cuts
        if word_cuts and job.align_words:
            word_cuts, aligned = refine.align_cuts(word_cuts, refine.envelope(info))
        found += word_cuts
    normalized = cutlist.normalize(found, info.duration)
    kept = 0
    if job.kept_cuts:
        before = len(normalized)
        normalized = [c for c in normalized if cut_key(c) not in job.kept_cuts]
        kept = before - len(normalized)
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
    return Plan(job, info, normalized, segments, output, words, aligned=aligned,
                word_cuts=len(word_cuts), kept=kept, long_found=long_found,
                ai_matches=ai_matches, ai_note=ai_note, edit_result=edit_result)


def ai_suggestions(words: list[transcribe.Word], duration: float,
                   ask_key: Callable[[], str | None] | None) -> tuple[list[review.Match], str]:
    """AI review suggestions matched to word times. Never fails the plan: returns (matches, note)."""
    key = keys.find_key() or None
    try:
        try:
            suggestions, cached = review.suggest(words, duration, key)
        except review.ReviewError:
            if key or not ask_key:
                raise
            suggestions, cached = review.suggest(words, duration, ask_key())
    except review.ReviewError as exc:
        return [], str(exc)
    from kv_media import ui
    if cached:
        ui.info(ui.dim("Using saved AI review - no Gemini call needed."))
    matches = review.match(suggestions, words)
    skipped = len(suggestions) - len(matches)
    note = f"{skipped} AI suggestion(s) could not be matched to the transcript and were ignored." if skipped else ""
    return matches, note


def find_edits(job: Job) -> Path | None:
    """A transcript of this source with ~~struck~~ words, in the output folder or next to the source."""
    export = paths.output_path(job.source, job.audio_only, job.fmt, job.output)
    return edits.find_edited(job.source, [export.parent, job.source.parent, Path.cwd()])


def write_transcript(job: Job, ask_key: Callable[[], str | None] | None = None) -> Path:
    """--transcript: only write <name>_transcript.md (uses the saved transcript when there is one)."""
    validate_source(job.source)
    ffmpeg.require_ffmpeg()
    info = ffmpeg.probe(job.source)
    if not info.has_audio:
        raise JobError(f"{job.source.name} has no audio track.")
    words = get_words(job, info, ask_key)
    if not words:
        raise JobError("The transcript is empty.")
    if job.output and job.output.lower().endswith(".md"):
        target = paths.clean_path(job.output)
    else:
        export = paths.output_path(job.source, job.audio_only, job.fmt, job.output)
        target = export.with_name(f"{job.source.stem}_transcript.md")
    return transcript_md.write(words, job.source, info.duration, target)


def suggest_titles(plan: Plan, ask_key: Callable[[], str | None] | None = None,
                   avoid: list[str] | None = None) -> titles.TitlePack:
    """AI title ideas + SEO pack from the transcript. Transcribes (once, cached) if needed."""
    key = keys.find_key() or (ask_key() if ask_key else None)
    if not key:
        raise JobError("AI titles need a Gemini API key. Set GEMINI_API_KEY "
                       f"(environment, a .env file, or {keys.CONFIG_ENV}) or run kvm to be asked.")
    if not plan.words:
        plan.words = get_words(plan.job, plan.info, ask_key)
    try:
        return titles.generate(plan.words, plan.original_duration, plan.with_video, key, avoid)
    except titles.TitleError as exc:
        raise JobError(str(exc)) from exc


def _output_is_explicit_file(job: Job) -> bool:
    """Same rule as paths.output_path: -o names a file unless it looks like a folder."""
    if not job.output or job.output.endswith(("/", "\\")):
        return False
    target = paths.clean_path(job.output)
    return bool(target.suffix) and not target.is_dir()


def apply_title(plan: Plan, title: str, pack: titles.TitlePack | None = None) -> None:
    """Use ``title`` for the file name (unless -o named a file) and the embedded metadata."""
    title = titles.clean_title(title)
    if not title:
        raise JobError("The title is empty.")
    if _output_is_explicit_file(plan.job):
        plan.title, plan.seo = title, pack
        return

    slug = titles.filename_slug(title)
    if pack is not None:
        base_dir = plan.output.parent.parent if plan.seo is not None else plan.output.parent
        current_dir = plan.output.parent if plan.seo is not None else None
        pack_dir = titles.unique_pack_dir(base_dir, slug, keep=current_dir)
        named = pack_dir / f"{pack_dir.name}{plan.output.suffix}"
    else:
        base_dir = plan.output.parent.parent if plan.seo is not None else plan.output.parent
        named = base_dir / f"{slug}{plan.output.suffix}"
        named = titles.unique_path(named)

    if named.resolve() == plan.job.source.resolve():
        named = named.with_name(f"{named.stem}{paths.SUFFIX}{named.suffix}")
    plan.title, plan.seo = title, pack
    plan.output = named


def execute(plan: Plan, on_progress: Callable[[float], None] | None = None) -> None:
    plan.output.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, str] = {}
    if plan.title:
        metadata["title"] = plan.title
        if plan.seo and plan.seo.description:
            metadata["comment"] = plan.seo.description
    render.export(plan.info, plan.segments, plan.output, plan.with_video,
                  work_dir=plan.output.parent, on_progress=on_progress, metadata=metadata)
    if plan.cuts:
        plan.report_path.write_text(report.build_report(plan), encoding="utf-8")
    if plan.words:
        transcript_md.write(plan.words, plan.job.source, plan.original_duration, plan.transcript_path)
    if plan.title and plan.seo:
        plan.seo_path.write_text(titles.build_seo_text(plan.seo, plan.title), encoding="utf-8")

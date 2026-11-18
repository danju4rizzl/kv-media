"""Command-line entry point for `kvm`.

    kvm                          interactive wizard
    kvm FILE                     wizard, starting with FILE
    kvm FILE --pauses --fillers --repeats   run directly, no questions
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from kv_media import __version__, ffmpeg, pauses, paths, pipeline, report, transcript_md, ui, wizard
from kv_media.pauses import PauseSettings

_DEFAULTS = PauseSettings()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kvm",
        description="Remove the unimportant parts of a recording. Run with no arguments for the wizard.",
    )
    p.add_argument("file", nargs="?", help="audio or video file (paste or drag the path)")
    p.add_argument("--pauses", action="store_true", help="remove pauses / dead air (runs without questions)")
    p.add_argument("--fillers", action="store_true", help="remove filler words: um, ah, ... (Gemini)")
    p.add_argument("--repeats", action="store_true",
                   help="remove repeated words/phrases and restarted sentences (Gemini)")
    p.add_argument("--all", action="store_true", help="same as --pauses --fillers --repeats")
    p.add_argument("--ai-review", action="store_true",
                   help="AI reads the transcript and cuts words that make no sense, stumbles and false "
                        "starts (cheap Gemini text call; see --ai-min-confidence). On automatically "
                        "with --fillers / --repeats / --all")
    p.add_argument("--no-ai-review", action="store_true",
                   help="do not run the AI review automatically with --fillers / --repeats "
                        "(or set KVM_AI_REVIEW=off)")
    p.add_argument("--ai-min-confidence", type=float, default=0.8, metavar="0-1",
                   help="only AI suggestions at least this sure are cut (default 0.8; --dry-run lists all)")
    p.add_argument("--words-file", metavar="PATH", help="your own filler list (default: fillers.txt)")
    p.add_argument("--edits", nargs="?", const="auto", metavar="MD",
                   help="cut the words you struck out (~~like this~~) in the transcript .md; without a "
                        "path kvm finds it in kvm_output/ (free, no Gemini call)")
    p.add_argument("--transcript", action="store_true",
                   help="only write the transcript as Markdown (<name>_transcript.md); no export")
    p.add_argument("--ai-title", action="store_true",
                   help="name the export with an AI title from the transcript + write an SEO pack (Gemini)")
    p.add_argument("--pack-dir", dest="pack_dir", action="store_true", default=None,
                   help="group pack outputs (video, transcript, report, SEO) into a subfolder "
                        "(default: on; or set KVM_PACK_DIR=on)")
    p.add_argument("--no-pack-dir", dest="pack_dir", action="store_false",
                   help="write pack outputs flat in the output folder instead of a subfolder "
                        "(or set KVM_PACK_DIR=off)")
    p.add_argument("--audio-only", action="store_true", help="export only the edited audio")
    p.add_argument("--format", metavar="EXT", help="audio format: wav, flac, mp3, m4a, ... (implies --audio-only)")
    p.add_argument("-o", "--output", metavar="PATH", help=f"output file or folder (default: ./{paths.OUTPUT_DIR}/)")
    p.add_argument("--silence-threshold", type=float, default=_DEFAULTS.threshold_db, metavar="DB",
                   help=f"quieter than this counts as a pause (default {_DEFAULTS.threshold_db:g})")
    p.add_argument("--min-pause", type=float, default=_DEFAULTS.min_pause, metavar="SEC",
                   help=f"only shorten pauses longer than this (default {_DEFAULTS.min_pause:g})")
    p.add_argument("--keep", type=float, default=_DEFAULTS.keep, metavar="SEC",
                   help=f"natural pause left on each side of a cut (default {_DEFAULTS.keep:g})")
    p.add_argument("--long-pauses", choices=pipeline.LONG_PAUSE_MODES, default="cut",
                   help="silences of 10s+ (often screen demos): cut, shorten or keep (default cut)")
    p.add_argument("--long-keep", type=float, default=2.0, metavar="SEC",
                   help="seconds of silence left by --long-pauses shorten (default 2)")
    p.add_argument("--no-align", action="store_true",
                   help="use Gemini's word times as-is (do not snap word cuts to the quiet gap)")
    p.add_argument("--dry-run", action="store_true", help="show the cuts without writing anything")
    p.add_argument("-v", "--version", action="version", version=f"kvm {__version__}")
    return p


def _job_from_args(args: argparse.Namespace) -> pipeline.Job:
    job = pipeline.Job(
        source=paths.clean_path(args.file) if args.file else Path(),
        audio_only=args.audio_only or bool(args.format),
        remove_pauses=args.pauses or args.all,
        remove_fillers=args.fillers or args.all,
        remove_repeats=args.repeats or args.all,
        words_file=paths.clean_path(args.words_file) if args.words_file else None,
        pause_settings=PauseSettings(args.silence_threshold, args.min_pause, args.keep),
        fmt=args.format,
        output=args.output,
        ai_title=args.ai_title,
        align_words=not args.no_align,
        ai_review=args.ai_review,
        ai_min_confidence=args.ai_min_confidence,
        long_pauses=args.long_pauses,
        long_keep=args.long_keep,
    )
    if not args.no_ai_review:
        job.apply_auto_ai_review()
    if args.pack_dir is not None:
        job.pack_dir = args.pack_dir
    if args.edits and args.edits != "auto":
        job.edits = paths.clean_path(args.edits)
    elif args.edits == "auto" and args.file:
        job.edits = pipeline.find_edits(job)
        if job.edits is None:
            raise pipeline.JobError(f"No transcript with struck words (~~ ~~) found for {job.source.name}. "
                                    f"Run kvm {job.source.name} --transcript, strike words, then try again.")
    return job


class _Progress:
    def __init__(self) -> None:
        self.done = False

    def __call__(self, fraction: float) -> None:
        if not self.done:
            ui.progress(fraction)
            self.done = fraction >= 1.0


def _export(plan: pipeline.Plan, step: int = 5) -> None:
    if not plan.cuts and not plan.job.audio_only:
        ui.warn("Nothing to cut, so no file was written.")
        return
    ui.step(step, "Export")
    started = time.monotonic()
    pipeline.execute(plan, _Progress())
    ui.info(f"{ui.green('Done')} in {time.monotonic() - started:.0f}s")
    ui.info(f"Saved   : {plan.output}")
    if plan.cuts:
        ui.info(f"Report  : {plan.report_path}")
    if plan.words:
        kept = " (not replaced: it has your ~~edits~~)" if transcript_md.is_edited(plan.transcript_path) else ""
        ui.info(f"Transcript: {plan.transcript_path}{kept}")
    if plan.title and plan.seo:
        ui.info(f"SEO     : {plan.seo_path}")


def _auto_title(plan: pipeline.Plan) -> None:
    """--ai-title: use the top-ranked title. A failure never blocks the export."""
    ui.info(ui.dim("Writing an AI title..."))
    try:
        pack = pipeline.suggest_titles(plan)
    except pipeline.JobError as exc:
        ui.warn(f"No AI title ({exc}). Keeping {plan.output.name}")
        return
    pipeline.apply_title(plan, pack.titles[0].text, pack)
    ui.info(f"Title           : {plan.title}")
    for t in pack.titles[1:]:
        ui.info(ui.dim(f"  also: [{t.style}] {t.text}"))


def _direct(job: pipeline.Job, dry_run: bool) -> None:
    plan = pipeline.make_plan(job)
    if job.ai_review and plan.ai_note:
        ui.warn(plan.ai_note)
    if job.ai_title:
        _auto_title(plan)
    for line in report.summary_lines(plan):
        ui.info(line)
    if not plan.cuts and job.remove_pauses and not job.needs_words:
        for line in pauses.no_pauses_hint(plan.info, job.pause_settings).splitlines():
            ui.warn(line)
    if dry_run:
        longest = report.longest_pauses(plan)
        if longest:
            print()
            ui.info("Longest pauses:")
            for line in longest:
                ui.info(line)
        print()
        for line in report.timeline(plan):
            ui.info(line)
        if plan.ai_matches:
            print()
            ui.info(f"AI suggestions (cut when confidence >= {job.ai_min_confidence:g}):")
            for line in report.ai_table(plan):
                ui.info(line)
        print()
        for line in report.cut_table(plan):
            ui.info(line)
        ui.info(ui.dim(f"Dry run: nothing written (would save {plan.output})"))
        return
    _export(plan)


def main(argv: list[str] | None = None) -> int:
    ffmpeg.enable_windows_ansi()
    args = build_parser().parse_args(argv)
    try:
        job = _job_from_args(args)
        if args.transcript:
            if not args.file:
                ui.error("Give a file, e.g. kvm talk.mp4 --transcript")
                return 2
            ui.info(f"Saved   : {pipeline.write_transcript(job)}")
            return 0
        if job.remove_pauses or job.needs_words:
            if not args.file:
                ui.error("Give a file to work on, e.g. kvm talk.mp4 --all")
                return 2
            _direct(job, args.dry_run)
        else:
            plan = wizard.run(job.source if args.file else None, base=job)
            if plan is None:
                ui.info("Cancelled.")
                return 0
            _export(plan, 6)
    except (pipeline.JobError, ffmpeg.FFmpegError, OSError) as exc:
        ui.error(str(exc))
        return 1
    except (KeyboardInterrupt, EOFError):
        print()
        ui.info("Cancelled.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())

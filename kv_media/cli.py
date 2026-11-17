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

from kv_media import __version__, ffmpeg, pauses, paths, pipeline, report, ui, wizard
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
    p.add_argument("--words-file", metavar="PATH", help="your own filler list (default: fillers.txt)")
    p.add_argument("--audio-only", action="store_true", help="export only the edited audio")
    p.add_argument("--format", metavar="EXT", help="audio format: wav, flac, mp3, m4a, ... (implies --audio-only)")
    p.add_argument("-o", "--output", metavar="PATH", help=f"output file or folder (default: ./{paths.OUTPUT_DIR}/)")
    p.add_argument("--silence-threshold", type=float, default=_DEFAULTS.threshold_db, metavar="DB",
                   help=f"quieter than this counts as a pause (default {_DEFAULTS.threshold_db:g})")
    p.add_argument("--min-pause", type=float, default=_DEFAULTS.min_pause, metavar="SEC",
                   help=f"only shorten pauses longer than this (default {_DEFAULTS.min_pause:g})")
    p.add_argument("--keep", type=float, default=_DEFAULTS.keep, metavar="SEC",
                   help=f"natural pause left on each side of a cut (default {_DEFAULTS.keep:g})")
    p.add_argument("--dry-run", action="store_true", help="show the cuts without writing anything")
    p.add_argument("-v", "--version", action="version", version=f"kvm {__version__}")
    return p


def _job_from_args(args: argparse.Namespace) -> pipeline.Job:
    return pipeline.Job(
        source=paths.clean_path(args.file) if args.file else Path(),
        audio_only=args.audio_only or bool(args.format),
        remove_pauses=args.pauses or args.all,
        remove_fillers=args.fillers or args.all,
        remove_repeats=args.repeats or args.all,
        words_file=paths.clean_path(args.words_file) if args.words_file else None,
        pause_settings=PauseSettings(args.silence_threshold, args.min_pause, args.keep),
        fmt=args.format,
        output=args.output,
    )


class _Progress:
    def __init__(self) -> None:
        self.done = False

    def __call__(self, fraction: float) -> None:
        if not self.done:
            ui.progress(fraction)
            self.done = fraction >= 1.0


def _export(plan: pipeline.Plan) -> None:
    if not plan.cuts and not plan.job.audio_only:
        ui.warn("Nothing to cut, so no file was written.")
        return
    ui.step(5, "Export")
    started = time.monotonic()
    pipeline.execute(plan, _Progress())
    ui.info(f"{ui.green('Done')} in {time.monotonic() - started:.0f}s")
    ui.info(f"Saved   : {plan.output}")
    if plan.cuts:
        ui.info(f"Report  : {plan.report_path}")


def _direct(job: pipeline.Job, dry_run: bool) -> None:
    plan = pipeline.make_plan(job)
    for line in report.summary_lines(plan):
        ui.info(line)
    if not plan.cuts and job.remove_pauses and not job.needs_words:
        for line in pauses.no_pauses_hint(plan.info, job.pause_settings).splitlines():
            ui.warn(line)
    if dry_run:
        print()
        for line in report.cut_table(plan):
            ui.info(line)
        ui.info(ui.dim(f"Dry run: nothing written (would save {plan.output})"))
        return
    _export(plan)


def main(argv: list[str] | None = None) -> int:
    ffmpeg.enable_windows_ansi()
    args = build_parser().parse_args(argv)
    job = _job_from_args(args)
    try:
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
            _export(plan)
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

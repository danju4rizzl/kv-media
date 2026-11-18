"""v0.3 tuning: content-keyed transcript cache, word cuts snapped to the audio, long silences,
preview timeline and keeping single cuts. Gemini is never called."""

import shutil
import subprocess
from pathlib import Path

import pytest

from kv_media import pauses, pipeline, refine, report, transcribe, wizard
from kv_media.cli import main
from kv_media.cuts import Cut
from kv_media.ffmpeg import MediaInfo
from kv_media.transcribe import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def _info(path: Path) -> MediaInfo:
    return MediaInfo(path, 10.0, 0, 48000, 1, "pcm", None, None, 0.0)


# ---- 1. transcript cache keyed by content ------------------------------------------

def test_cache_hits_for_the_same_file_at_another_path(tmp_path):
    a = tmp_path / "one" / "talk.mp4"
    a.parent.mkdir()
    a.write_bytes(b"same bytes" * 1000)
    b = tmp_path / "two" / "renamed.mp4"
    b.parent.mkdir()
    shutil.copy(a, b)
    transcribe.save_cached(_info(a), [Word("hi", 0, 1)])
    assert transcribe.load_cached(_info(b)) == [Word("hi", 0, 1)]


def test_cache_misses_when_content_changes(tmp_path):
    f = tmp_path / "talk.mp4"
    f.write_bytes(b"a" * 5000)
    transcribe.save_cached(_info(f), [Word("hi", 0, 1)])
    f.write_bytes(b"b" * 5000)
    assert transcribe.load_cached(_info(f)) is None


def test_cache_depends_on_model(tmp_path, monkeypatch):
    f = tmp_path / "talk.mp4"
    f.write_bytes(b"a" * 5000)
    transcribe.save_cached(_info(f), [Word("hi", 0, 1)])
    monkeypatch.setenv("GEMINI_MODEL", "other-model")
    assert transcribe.load_cached(_info(f)) is None


def test_legacy_cache_is_read_and_migrated(tmp_path):
    f = tmp_path / "talk.mp4"
    f.write_bytes(b"a" * 5000)
    legacy = transcribe._legacy_cache_path(_info(f))
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"words": [{"text": "old", "start": 0, "end": 1}]}', encoding="utf-8")
    assert transcribe.load_cached(_info(f)) == [Word("old", 0, 1)]
    assert (transcribe.cache_dir() / transcribe._cache_name(_info(f))).exists()


def test_shared_cache_dir_is_read_and_written(tmp_path, monkeypatch):
    shared = tmp_path / "windows-cache"
    shared.mkdir()
    monkeypatch.setattr(transcribe, "shared_cache_dirs", lambda: [shared])
    f = tmp_path / "talk.mp4"
    f.write_bytes(b"a" * 5000)
    transcribe.save_cached(_info(f), [Word("hi", 0, 1)])
    name = transcribe._cache_name(_info(f))
    assert (shared / name).exists()
    (transcribe.cache_dir() / name).unlink()
    assert transcribe.load_cached(_info(f)) == [Word("hi", 0, 1)]     # found in the shared one


def test_kvm_cache_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("KVM_CACHE_DIR", str(tmp_path / "mycache"))
    assert transcribe.cache_dir() == tmp_path / "mycache" / "transcripts"


# ---- 2. word cuts snapped to the quiet gap --------------------------------------------

def _levels(spec: list[tuple[float, float, float]], total: float = 3.0) -> list[float]:
    """Envelope with -80 dB everywhere except (start, end, dB) spans."""
    levels = [-80.0] * round(total / refine.FRAME_SECONDS)
    for start, end, db in spec:
        for i in range(round(start / refine.FRAME_SECONDS), round(end / refine.FRAME_SECONDS)):
            levels[i] = db
    return levels


def test_parse_envelope():
    text = ("frame:0 pts:0 pts_time:0\nlavfi.astats.Overall.RMS_level=-inf\n"
            "frame:1\nlavfi.astats.Overall.RMS_level=-23.5\nframe:2\nlavfi.astats.Overall.RMS_level=nan\n")
    assert refine.parse_envelope(text) == [-100.0, -23.5, -100.0]


def test_snap_moves_a_late_end_out_of_the_word():
    # "um" really sounds 1.00-1.40; Gemini says it ends at 1.30 (inside the sound).
    levels = _levels([(1.0, 1.4, -20), (1.55, 2.0, -20)])
    end = refine.snap(1.30, levels, 1.2, 9)
    assert 1.40 <= end <= 1.55


def test_snap_moves_an_early_start_back_to_the_gap():
    levels = _levels([(0.2, 0.9, -20), (1.0, 1.4, -20)])     # previous word, then the filler
    start = refine.snap(1.10, levels, 0, 1.2)
    assert 0.9 <= start <= 1.0


def test_snap_keeps_time_when_nothing_is_quiet():
    levels = _levels([(0.0, 3.0, -20)])
    assert refine.snap(1.23, levels, 0, 9) == 1.23


def test_snap_respects_bounds():
    levels = _levels([(1.0, 1.4, -20)])
    assert refine.snap(1.05, levels, 1.02, 1.2) >= 1.02


def test_align_cuts_counts_moves_and_never_inverts():
    levels = _levels([(1.0, 1.4, -20), (1.55, 2.0, -20)])
    cuts, moved = refine.align_cuts([Cut(1.05, 1.30, "filler", "um"), Cut(2.5, 2.5, "filler", "uh")], levels)
    assert moved >= 1
    assert all(c.end - c.start >= 0 for c in cuts)
    assert cuts[0].start <= 1.0 and cuts[0].end >= 1.4


@needs_ffmpeg
def test_envelope_from_real_audio_and_alignment(tmp_path):
    tone = "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,3.5)+gte(t,5.5))"
    src = tmp_path / "v.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"aevalsrc=exprs='{tone}':s=48000:d=6.5", str(src)], check=True)
    info = MediaInfo(src, 6.5, 0, 48000, 1, "pcm_s16le", None, None, 0.0)
    levels = refine.envelope(info)
    assert abs(len(levels) * refine.FRAME_SECONDS - 6.5) < 0.05
    assert levels[50] > -20 and levels[180] <= -90                      # tone vs silence
    # Gemini says the word is 2.45-3.45; the sound is really 2.5-3.5.
    (cut,), moved = refine.align_cuts([Cut(2.45, 3.45, "filler", "um")], levels)
    assert moved == 1 and cut.start <= 2.5 and cut.end >= 3.5


# ---- 4. long silences, timeline, longest pauses, keeping cuts --------------------------

def test_long_pause_modes():
    cuts = [Cut(1.2, 12.8, "pause"), Cut(20, 21, "pause")]
    assert pauses.apply_long_mode(cuts, "cut", 2) == cuts
    assert pauses.apply_long_mode(cuts, "keep", 2) == [cuts[1]]
    assert pauses.apply_long_mode(cuts, "shorten", 2) == [Cut(2.2, 11.8, "pause"), cuts[1]]
    with pytest.raises(ValueError):
        pauses.apply_long_mode(cuts, "bogus", 2)


class _Plan:
    """Just enough of a Plan for the report helpers."""
    def __init__(self, cuts, long_found=(), duration=60.0):
        self.cuts, self.long_found, self.original_duration = list(cuts), list(long_found), duration


def test_timeline_marks_where_cuts_are():
    line = report.timeline(_Plan([Cut(0, 30, "pause"), Cut(45, 47, "filler")]), width=4)[0]
    assert "|##=+|" in line


def test_longest_pauses_sorted_and_not_duplicated():
    long_cut = Cut(10, 30, "pause")
    plan = _Plan([Cut(11, 29, "pause"), Cut(40, 45, "pause"), Cut(50, 50.5, "pause"), Cut(52, 55, "filler")],
                 long_found=[long_cut])
    rows = report.longest_pauses(plan)
    assert len(rows) == 2 and "20.0s" in rows[0] and "5.0s" in rows[1]


@pytest.mark.parametrize("raw, count, expected", [
    ("3", 5, {3}), ("1, 3-4", 5, {1, 3, 4}), ("2-2", 5, {2}),
    ("0", 5, None), ("6", 5, None), ("4-2", 5, None), ("x", 5, None), ("", 5, None),
])
def test_parse_numbers(raw, count, expected):
    assert wizard.parse_numbers(raw, count) == expected


def _clip(tmp_path: Path, expr: str, seconds: float, name="clip.mp4") -> Path:
    src = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}|{expr}':s=48000:d={seconds}",
                    "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)],
                   check=True)
    return src


@needs_ffmpeg
def test_long_silence_found_and_modes_in_plan(tmp_path):
    src = _clip(tmp_path, "0.5*sin(2*PI*440*t)*(lt(t,1)+gte(t,13))", 20)
    job = pipeline.Job(source=src)
    plan = pipeline.make_plan(job)
    assert len(plan.long_found) == 1 and plan.long_found[0].duration > 10
    assert plan.removed > 10
    job.long_pauses = "keep"
    assert pipeline.make_plan(job).removed < 1
    job.long_pauses, job.long_keep = "shorten", 3
    shortened = pipeline.make_plan(job)
    assert abs(plan.removed - shortened.removed - 3) < 0.15


@needs_ffmpeg
def test_wizard_keep_one_cut(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clip(tmp_path, "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,3.5)+gte(t,5.5))", 6.5)
    # file 1, video, pauses only, keep cut 1, export, no AI title
    answers = iter(["1", "1", "1", "k", "1", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    out = tmp_path / "kvm_output" / "clip_kvm.mp4"
    report_text = (tmp_path / "kvm_output" / "clip_kvm_cuts.txt").read_text(encoding="utf-8")
    assert "Kept by you     : 1 cut left in" in report_text
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True, check=True).stdout
    assert abs(float(probe) - 4.9) < 0.1          # only the second pause (1.6 s) was removed


@needs_ffmpeg
def test_dry_run_shows_timeline_and_longest_pauses(tmp_path, capsys):
    src = _clip(tmp_path, "0.5*sin(2*PI*440*t)*(lt(t,1)+gte(t,13))", 20)
    assert main([str(src), "--pauses", "--dry-run", "--long-pauses", "shorten"]) == 0
    out = capsys.readouterr().out
    assert "Longest pauses:" in out and "mostly cut" in out and "shortened to 2s" in out

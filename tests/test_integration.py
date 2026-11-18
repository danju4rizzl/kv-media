"""End-to-end runs on a generated clip. Skipped when FFmpeg is missing."""

import shutil
import subprocess
from pathlib import Path

import pytest

from kv_media import ffmpeg, pipeline, titles, transcribe
from kv_media.cli import main
from kv_media.transcribe import Word

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")

# Tone at 0-1 s, 2.5-3.5 s and 5.5-6.5 s; silence in between (1.5 s and 2 s gaps).
TONE = "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,3.5)+gte(t,5.5))"
# Defaults keep 0.2 s each side: cuts 1.2-2.3 and 3.7-5.3 -> 2.7 s removed -> 3.8 s left.
EXPECTED = 3.8


def _make(path: Path, video: bool) -> Path:
    args = ["ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{TONE}|{TONE}':s=48000:d=6.5"]
    if video:
        args += ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6.5",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
    args.append(str(path))
    subprocess.run(args, check=True)
    return path


def _stream_duration(path: Path, kind: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries",
                          "stream=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out.splitlines()[0])


@pytest.fixture
def video(tmp_path):
    return _make(tmp_path / "clip.mp4", video=True)


def test_video_pauses_removed_in_sync(video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plan = pipeline.make_plan(pipeline.Job(source=video))
    assert len(plan.cuts) == 2
    pipeline.execute(plan)

    out = tmp_path / "kvm_output" / "clip_kvm.mp4"
    assert out.exists() and plan.report_path.exists()
    info = ffmpeg.probe(out)
    assert info.duration == pytest.approx(EXPECTED, abs=0.1)
    assert info.sample_rate == 48000 and info.channels == 2       # nothing downmixed / resampled
    a, v = _stream_duration(out, "a:0"), _stream_duration(out, "v:0")
    assert abs(a - v) < 0.05                                       # in sync
    assert "1.10s  pause" in plan.report_path.read_text(encoding="utf-8")


def test_short_last_piece_keeps_its_audio(tmp_path, monkeypatch):
    """Regression: FFmpeg 6.1 dropped a short final piece when audio+video were filtered together."""
    monkeypatch.chdir(tmp_path)
    tone = "0.5*sin(2*PI*440*t)*(lt(t,3)+gte(t,5.7))"     # 3 s tone, 2.7 s gap, 0.3 s tone at the end
    src = tmp_path / "tail.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"aevalsrc=exprs='{tone}|{tone}':s=48000:d=6",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    plan = pipeline.make_plan(pipeline.Job(source=src))
    pipeline.execute(plan)
    out = tmp_path / "kvm_output" / "tail_kvm.mp4"
    a, v = _stream_duration(out, "a:0"), _stream_duration(out, "v:0")
    assert v == pytest.approx(plan.new_duration, abs=0.05)
    assert abs(a - v) < 0.05


def test_audio_only_from_video_to_wav(video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([str(video), "--pauses", "--format", "wav"]) == 0
    info = ffmpeg.probe(tmp_path / "kvm_output" / "clip_kvm.wav")
    assert not info.has_video and info.audio_codec == "pcm_s24le"
    assert info.duration == pytest.approx(EXPECTED, abs=0.1)


def test_audio_file_keeps_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = _make(tmp_path / "voice.flac", video=False)
    assert main([str(src), "--pauses"]) == 0
    info = ffmpeg.probe(tmp_path / "kvm_output" / "voice_kvm.flac")
    assert info.audio_codec == "flac" and info.sample_rate == 48000
    assert info.duration == pytest.approx(EXPECTED, abs=0.05)


def test_dry_run_writes_nothing(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(video), "--pauses", "--dry-run"]) == 0
    assert not (tmp_path / "kvm_output").exists()
    assert "Removed" in capsys.readouterr().out


def test_wizard_end_to_end(video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # step1: paste quoted path, step2: video, step3: pauses only, step4: export, step5: no AI title
    answers = iter([f'"{video}"', "1", "1", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    assert (tmp_path / "kvm_output" / "clip_kvm.mp4").exists()


def test_missing_file_is_a_clean_error(tmp_path, capsys):
    assert main([str(tmp_path / "nope.mp4"), "--pauses"]) == 1
    assert "File not found" in capsys.readouterr().err


def test_wizard_explains_when_no_pauses(tmp_path, monkeypatch, capsys):
    """A recording with no dead air must not offer an Export that silently writes nothing."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "busy.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=f=300:r=48000:d=3",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    answers = iter(["1", "1", "1", "q"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "No pauses longer than" in out and "Longest quiet stretch" in out
    assert "e) Export" not in out


def _fake_transcript(monkeypatch, words):
    calls = []

    def fake(info, api_key, workers=4):
        calls.append(api_key)
        transcribe.save_cached(info, words)
        return words
    monkeypatch.setattr(transcribe, "transcribe", fake)
    return calls


def test_fillers_and_repeats_cut_from_video(video, tmp_path, monkeypatch):
    """Tone 1 (0-1 s) holds 'um', tone 2 (2.5-3.5 s) holds a repeat; both get cut."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    _fake_transcript(monkeypatch, [
        Word("Hello", 0.0, 0.5), Word("um", 0.5, 0.95),
        Word("the", 2.5, 2.7), Word("the", 2.8, 3.0), Word("code", 3.0, 3.5),
        Word("done.", 5.5, 6.4),
    ])
    job = pipeline.Job(source=video, remove_pauses=True, remove_fillers=True, remove_repeats=True)
    plan = pipeline.make_plan(job)
    reasons = {c.reason for c in plan.cuts}
    assert any("filler" in r for r in reasons) and "repeat" in reasons
    pipeline.execute(plan)
    report = plan.report_path.read_text(encoding="utf-8")
    assert '"um"' in report and '"the"' in report
    out = ffmpeg.probe(plan.output)
    assert out.duration == pytest.approx(plan.new_duration, abs=0.1)
    assert out.duration < EXPECTED - 0.3


def test_wizard_all_three_with_key_prompt(video, tmp_path, monkeypatch):
    """No key anywhere: the wizard asks (hidden), saves it, and the transcript is reused on re-plan."""
    monkeypatch.chdir(tmp_path)
    calls = _fake_transcript(monkeypatch, [Word("um", 0.2, 0.6), Word("okay.", 2.6, 3.2)])
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": "typed-key")
    # file, video, all three (default), save key? y, preview -> change removals -> 1,2 -> export, no title
    answers = iter(["1", "1", "", "y", "c", "1,2", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    assert calls == ["typed-key"]                      # transcribed once, reused after the change
    assert (tmp_path / "kvm_output" / "clip_kvm.mp4").exists()


def test_direct_mode_without_key_is_a_clean_error(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(video), "--fillers"]) == 1
    assert "Gemini API key" in capsys.readouterr().err


# ---- AI title (Gemini mocked) -------------------------------------------------------

PACK_JSON = """{"titles": [
  {"title": "How I Cut Dead Air From Videos in 5 Minutes", "style": "how-to", "why": "clear outcome"},
  {"title": "Why Your Videos Feel Slow (and the Easy Fix)", "style": "curiosity", "why": "gap"}],
 "keyword": "cut dead air", "description": "Learn to cut dead air fast. Short and clear.",
 "tags": ["cut dead air", "video editing"], "hashtags": ["#videoediting", "editing tips"]}"""


def _fake_titles(monkeypatch, fail=False):
    calls = []

    def fake(words, duration, is_video, api_key, avoid=None):
        calls.append({"words": len(words), "avoid": avoid})
        if fail:
            raise titles.TitleError("Gemini title request failed: 503")
        return titles.parse_pack(PACK_JSON)
    monkeypatch.setattr(titles, "generate", fake)
    return calls


def _probe_title(path: Path) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=title",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                          check=True).stdout.strip()


def test_wizard_ai_title_pick_edit_and_export(video, tmp_path, monkeypatch, capsys):
    """Pause-only run: the title step transcribes once, then names + tags the export."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    transcripts = _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3)
                                                 for i, w in enumerate("today I show how to cut dead air".split())])
    calls = _fake_titles(monkeypatch)
    # file, video, pauses only, export, AI title? y, new ideas, pick 2, keep text as is
    answers = iter(["1", "1", "1", "e", "y", "r", "2", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    assert transcripts == ["k"]                          # transcribed once for the title
    assert calls[1]["avoid"] and len(calls) == 2         # regenerate passes the rejected ideas
    pack_dir = tmp_path / "kvm_output" / "why-your-videos-feel-slow-and-the-easy-fix"
    out_file = pack_dir / "why-your-videos-feel-slow-and-the-easy-fix.mp4"
    assert out_file.exists()
    assert _probe_title(out_file) == "Why Your Videos Feel Slow (and the Easy Fix)"
    seo = (pack_dir / "why-your-videos-feel-slow-and-the-easy-fix_seo.txt").read_text(encoding="utf-8")
    assert "#videoediting #editingtips" in seo and "cut dead air, video editing" in seo
    assert "[how-to] How I Cut Dead Air" in seo
    report = (pack_dir / "why-your-videos-feel-slow-and-the-easy-fix_cuts.txt").read_text(encoding="utf-8")
    assert "Title  : Why Your Videos Feel Slow" in report


def test_wizard_ai_title_write_own(video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("a b c d e f".split())])
    _fake_titles(monkeypatch)
    answers = iter(["1", "1", "1", "e", "y", "w", "My Own Title: Café & Co"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    assert (tmp_path / "kvm_output" / "my-own-title-cafe-and-co" / "my-own-title-cafe-and-co.mp4").exists()


def test_direct_ai_title_uses_top_title(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("a b c d e f".split())])
    _fake_titles(monkeypatch)
    assert main([str(video), "--pauses", "--ai-title"]) == 0
    pack_dir = tmp_path / "kvm_output" / "how-i-cut-dead-air-from-videos-in-5-minutes"
    out_file = pack_dir / "how-i-cut-dead-air-from-videos-in-5-minutes.mp4"
    assert out_file.exists() and (pack_dir / f"{out_file.stem}_seo.txt").exists()
    assert "also: [curiosity]" in capsys.readouterr().out


def test_direct_ai_title_respects_no_pack_dir(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("a b c d e f".split())])
    _fake_titles(monkeypatch)
    assert main([str(video), "--pauses", "--ai-title", "--no-pack-dir"]) == 0
    flat_file = tmp_path / "kvm_output" / "how-i-cut-dead-air-from-videos-in-5-minutes.mp4"
    flat_seo = tmp_path / "kvm_output" / "how-i-cut-dead-air-from-videos-in-5-minutes_seo.txt"
    assert flat_file.exists() and flat_seo.exists()
    assert not (tmp_path / "kvm_output" / "how-i-cut-dead-air-from-videos-in-5-minutes").is_dir()


def test_direct_ai_title_failure_still_exports(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("a b c d e f".split())])
    _fake_titles(monkeypatch, fail=True)
    assert main([str(video), "--pauses", "--ai-title"]) == 0
    assert (tmp_path / "kvm_output" / "clip_kvm.mp4").exists()
    assert "No AI title" in capsys.readouterr().out


def test_ai_title_respects_explicit_output_file(video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    _fake_transcript(monkeypatch, [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("a b c d e f".split())])
    _fake_titles(monkeypatch)
    target = tmp_path / "final.mp4"
    assert main([str(video), "--pauses", "--ai-title", "-o", str(target)]) == 0
    assert target.exists() and _probe_title(target) == "How I Cut Dead Air From Videos in 5 Minutes"


def test_ai_title_without_key_is_a_warning_not_a_failure(video, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(video), "--pauses", "--ai-title"]) == 0
    assert "AI titles need a Gemini API key" in capsys.readouterr().out
    assert (tmp_path / "kvm_output" / "clip_kvm.mp4").exists()

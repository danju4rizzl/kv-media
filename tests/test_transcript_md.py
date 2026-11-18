"""Markdown transcript. Gemini is never called (cached / fake words only)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from kv_media import transcribe, transcript_md
from kv_media.cli import main
from kv_media.transcribe import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def W(text, start, end):
    return Word(text, start, end)


WORDS = [W("So", 0.0, 0.2), W("um", 0.3, 0.5), W("today", 0.6, 0.9), W("we", 1.0, 1.1),
         W("start.", 1.2, 1.5),
         W("Next", 5.0, 5.2), W("we", 5.3, 5.4), W("wait", 6.6, 6.9), W("here?", 7.0, 7.3)]


def test_lines_split_on_sentences_and_long_gaps():
    lines = transcript_md.split_lines(WORDS)
    assert [(l.number, l.first, l.last) for l in lines] == [(1, 0, 4), (2, 5, 8)]
    assert lines[1].start == 5.0 and lines[1].end == 7.3


def test_long_line_is_capped(monkeypatch):
    monkeypatch.setattr(transcript_md, "MAX_WORDS", 3)
    words = [W(f"w{i}", i * 0.3, i * 0.3 + 0.2) for i in range(7)]
    assert [(l.first, l.last) for l in transcript_md.split_lines(words)] == [(0, 2), (3, 5), (6, 6)]


def test_markdown_is_verbatim_numbered_and_marks_pauses():
    md = transcript_md.build(WORDS, Path("/x/My Talk.mp4"), 12.0)
    assert md.startswith("# Transcript: My Talk\n")
    assert "[1] (00:00.0) So um today we start." in md                 # fillers kept
    assert "*[pause 3.5s]*" in md                                       # gap between lines
    assert "[2] (00:05.0) Next we [pause 1.2s] wait here?" in md        # gap inside a line
    assert "*[pause 4.7s to the end]*" in md
    assert "9 words, 2 lines" in md


def test_path_for():
    assert transcript_md.path_for(Path("out/talk_kvm.mp4")) == Path("out/talk_kvm_transcript.md")


@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "clip.mp4"
    tone = "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,3.5)+gte(t,5.5))"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"aevalsrc=exprs='{tone}':s=48000:d=6.5",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=6.5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


@needs_ffmpeg
def test_transcript_only_uses_saved_transcript(clip, tmp_path, monkeypatch, capsys):
    from kv_media import ffmpeg
    monkeypatch.chdir(tmp_path)
    transcribe.save_cached(ffmpeg.probe(clip), WORDS)
    assert main([str(clip), "--transcript"]) == 0
    md = (tmp_path / "kvm_output" / "clip_transcript.md").read_text(encoding="utf-8")
    assert "[2] (00:05.0) Next we" in md
    assert not (tmp_path / "kvm_output" / "clip_kvm.mp4").exists()     # no export


@needs_ffmpeg
def test_transcript_only_to_named_md(clip, tmp_path, monkeypatch):
    from kv_media import ffmpeg
    monkeypatch.chdir(tmp_path)
    transcribe.save_cached(ffmpeg.probe(clip), WORDS)
    assert main([str(clip), "--transcript", "-o", "notes/talk.md"]) == 0
    assert (tmp_path / "notes" / "talk.md").exists()


@needs_ffmpeg
def test_transcript_only_without_key_or_cache_is_a_clean_error(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([str(clip), "--transcript"]) == 1
    assert "needs a Gemini API key" in capsys.readouterr().err


@needs_ffmpeg
def test_export_with_words_writes_transcript(clip, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(transcribe, "transcribe", lambda info, key, workers=4: [W("um", 0.2, 0.6), W("okay.", 2.6, 3.2)])
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert main([str(clip), "--pauses", "--fillers"]) == 0
    md = (tmp_path / "kvm_output" / "clip_kvm_transcript.md").read_text(encoding="utf-8")
    assert "[1] (00:00.2) um\n\n*[pause 2.0s]*\n\n[2] (00:02.6) okay." in md

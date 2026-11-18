"""Cut by striking words out in the Markdown transcript. Gemini is never called."""

import shutil
import subprocess
from pathlib import Path

import pytest

from kv_media import edits, pipeline, transcribe, transcript_md, wizard
from kv_media.cli import main
from kv_media.transcribe import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def words_from(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    return [Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]


# line 1: "I would leave the link in the in in the course material."
# line 2: "We use the blorf connectors today."
WORDS = words_from("I would leave the link in the in in the course material. We use the blorf connectors today.")


def md(words=WORDS) -> str:
    return transcript_md.build(words, Path("clip.mp4"), 20.0)


def strike(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


def cut_texts(result) -> list[str]:
    return [c.text for c in result.cuts]


# ---- parsing / mapping ----------------------------------------------------------------

def test_no_strikes_means_nothing():
    assert not edits.has_strikes(md())
    assert edits.edit_cuts(md(), WORDS).cuts == []


def test_struck_words_are_cut_at_the_exact_position():
    # "in" and "the" appear several times: the struck ones (the 2nd "the in") are cut, not the first.
    text = strike(md(), "link in the in in the course", "link in ~~the in~~ in the course")
    result = edits.edit_cuts(text, WORDS)
    assert cut_texts(result) == ["the in"]
    assert result.cuts[0].start == WORDS[6].start and result.cuts[0].end == WORDS[7].end
    assert result.cuts[0].reason == "edit" and result.struck_words == 2 and result.lines == 1


def test_whole_line_strike_and_strikes_over_two_lines_join():
    text = strike(md(), "[2] (", "~~[2] (")
    text = strike(text, "connectors today.", "connectors today.~~")
    assert cut_texts(edits.edit_cuts(text, WORDS)) == ["We use the blorf connectors today."]

    text = strike(md(), "course material.", "course ~~material.")
    text = strike(text, "We use the", "We use~~ the")
    assert cut_texts(edits.edit_cuts(text, WORDS)) == ["material. We use"]      # one cut, not two


def test_pause_marks_inside_a_strike_are_ignored():
    words = words_from("so what we", 0) + words_from("so today we start.", 3.0)
    text = strike(md(words), "what we [pause", "~~what we [pause")
    text = strike(text, "so today", "so~~ today")
    assert cut_texts(edits.edit_cuts(text, words)) == ["what we so"]


def test_line_with_fixed_typos_falls_back_to_quoting():
    text = strike(md(), "We use the blorf connectors", "We use the ~~blorf~~ Lovable connectors")
    result = edits.edit_cuts(text, WORDS)
    assert cut_texts(result) == ["blorf"] and result.not_found == []


def test_unknown_struck_text_is_reported_not_guessed():
    text = strike(md(), "We use the blorf connectors", "We ~~totally~~ use the blorf connectors")
    result = edits.edit_cuts(text, WORDS)
    assert result.cuts == [] and result.not_found == ['line 2: "totally"']


def test_unclosed_strike_is_an_error():
    text = strike(md(), "We use the", "We ~~use the")
    with pytest.raises(edits.EditError, match="never closed"):
        edits.edit_cuts(text, WORDS)


def test_source_name_and_find_edited(tmp_path):
    out = tmp_path / "kvm_output"
    out.mkdir()
    (out / "clip_kvm_transcript.md").write_text(md(), encoding="utf-8")                 # no strikes
    assert edits.find_edited(Path("clip.mp4"), [out]) is None
    edited = out / "My title_transcript.md"
    edited.write_text(strike(md(), "blorf", "~~blorf~~"), encoding="utf-8")
    assert edits.source_name(edited.read_text()) == "clip.mp4"
    assert edits.find_edited(Path("clip.mp4"), [out, tmp_path / "missing"]) == edited
    assert edits.find_edited(Path("other.mp4"), [out]) is None

    # Also finds edited transcripts inside a pack subdirectory
    edited.unlink()
    sub = out / "my-pack"
    sub.mkdir()
    sub_edited = sub / "my-pack_transcript.md"
    sub_edited.write_text(strike(md(), "blorf", "~~blorf~~"), encoding="utf-8")
    assert edits.find_edited(Path("clip.mp4"), [out]) == sub_edited


def test_edited_transcript_is_never_overwritten(tmp_path):
    target = tmp_path / "t_transcript.md"
    edited = strike(md(), "blorf", "~~blorf~~")
    target.write_text(edited, encoding="utf-8")
    transcript_md.write(WORDS, Path("clip.mp4"), 20.0, target)
    assert target.read_text(encoding="utf-8") == edited
    plain = tmp_path / "plain_transcript.md"
    plain.write_text("old", encoding="utf-8")
    transcript_md.write(WORDS, Path("clip.mp4"), 20.0, plain)
    assert plain.read_text(encoding="utf-8") == md()


# ---- wizard default --------------------------------------------------------------------

def test_wizard_default_selects_every_option(monkeypatch):
    assert wizard.default_removals() == ",".join(k for k, _, _ in wizard.REMOVALS) == "1,2,3,4"
    monkeypatch.setenv("KVM_AI_REVIEW", "off")
    assert wizard.default_removals() == "1,2,3"


# ---- end to end ------------------------------------------------------------------------

@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "aevalsrc=exprs='0.5*sin(2*PI*440*t)':s=48000:d=20",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


def _saved_transcript(monkeypatch):
    def no_gemini(*a, **k):
        raise AssertionError("Gemini must not be called")
    monkeypatch.setattr(transcribe, "transcribe", no_gemini)
    monkeypatch.setattr(transcribe, "load_cached", lambda info: WORDS)


@needs_ffmpeg
def test_cli_edits_auto_finds_and_cuts(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _saved_transcript(monkeypatch)
    out = tmp_path / "kvm_output"
    out.mkdir()
    (out / "clip_kvm_transcript.md").write_text(strike(md(), "the blorf", "the ~~blorf~~"), encoding="utf-8")
    assert main([str(clip), "--edits", "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "Your edits      : 1 struck word(s) on 1 line(s)" in text and "1 your edits" in text


@needs_ffmpeg
def test_cli_edits_without_strikes_explains(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _saved_transcript(monkeypatch)
    assert main([str(clip), "--edits"]) == 1
    captured = capsys.readouterr()
    assert "--transcript, strike words" in captured.out + captured.err


@needs_ffmpeg
def test_wizard_offers_edits_and_keeps_the_edited_file(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _saved_transcript(monkeypatch)
    out = tmp_path / "kvm_output"
    out.mkdir()
    edited = strike(md(), "the blorf", "the ~~blorf~~")
    (out / "clip_kvm_transcript.md").write_text(edited, encoding="utf-8")
    # file 1, video, use edits (Enter = y), removals: only edits (no pauses here: steady tone)
    answers = iter(["1", "1", "", "1", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    printed = capsys.readouterr().out
    assert "Found your transcript edits" in printed and "not replaced" in printed
    report = (out / "clip_kvm_cuts.txt").read_text(encoding="utf-8")
    assert "your edits" in report and "blorf" in report
    assert (out / "clip_kvm_transcript.md").read_text(encoding="utf-8") == edited

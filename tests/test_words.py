"""Fillers, repeats, restarts, transcript parsing and key lookup. Gemini is never called."""

import os
from types import SimpleNamespace as NS

import pytest

from kv_media import disfluency, keys, transcribe
from kv_media.transcribe import Word


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    out = []
    for i, w in enumerate(text.split()):
        out.append(Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)))
    return out


@pytest.fixture
def fillers():
    return disfluency.load_fillers(disfluency.DEFAULT_FILLERS_FILE)


# ---- fillers -----------------------------------------------------------------

@pytest.mark.parametrize("w", ["um", "Umm,", "ummmmm", "uh", "uhhh", "Uhm", "ah", "ahhhh", "er", "erm",
                               "hmm", "mmm", "eh"])
def test_stretched_fillers_are_found(fillers, w):
    cuts = disfluency.find_filler_cuts(words(f"so {w} we start"), fillers)
    assert [c.text for c in cuts] == [w]


@pytest.mark.parametrize("w", ["a", "I", "um-brella", "hum", "me", "earth", "her", "summer", "aha", "m"])
def test_real_words_are_not_fillers(fillers, w):
    assert disfluency.find_filler_cuts(words(f"so {w} we start"), fillers) == []


def test_truncated_word_is_cut(fillers):
    cuts = disfluency.find_filler_cuts(words("we wa- want this"), fillers)
    assert [c.text for c in cuts] == ["wa-"]


def test_custom_phrase_list(tmp_path):
    f = tmp_path / "mine.txt"
    f.write_text("# my list\nyou know\nbasically\n", encoding="utf-8")
    custom = disfluency.load_fillers(f)
    cuts = disfluency.find_filler_cuts(words("it is basically, you know, done"), custom)
    assert [c.text for c in cuts] == ["basically,", "you know,"]
    phrase = cuts[1]
    assert (phrase.start, phrase.end) == (1.2, 1.9)       # spans both words


def test_fillers_txt_in_current_folder_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fillers.txt").write_text("like\n", encoding="utf-8")
    assert disfluency.fillers_source() == tmp_path / "fillers.txt"
    assert [c.text for c in disfluency.find_filler_cuts(words("it is like um"), disfluency.load_fillers())] == ["like"]


# ---- repeats -----------------------------------------------------------------

def test_repeated_phrase_keeps_last_take(fillers):
    w = words("I just want to I just want to click here")
    cuts = disfluency.find_repeat_cuts(w, fillers)
    assert len(cuts) == 1
    assert (cuts[0].start, cuts[0].end) == (w[0].start, w[4].start)
    assert cuts[0].text == "I just want to"


def test_three_takes_drop_first_two(fillers):
    w = words("the the the code")
    cuts = disfluency.find_repeat_cuts(w, fillers)
    assert [(c.start, c.end) for c in cuts] == [(w[0].start, w[1].start), (w[1].start, w[2].start)]


def test_repeat_with_filler_between(fillers):
    w = words("I just um I just did it")
    assert len(disfluency.find_repeat_cuts(w, fillers)) == 1


def test_long_pause_means_deliberate_repeat(fillers):
    w = [Word("again", 0.0, 0.4), Word("again", 2.5, 2.9)]
    assert disfluency.find_repeat_cuts(w, fillers) == []


def test_unrepeated_speech_is_untouched(fillers):
    assert disfluency.find_repeat_cuts(words("we open the file and then we save it"), fillers) == []


# ---- restarts ------------------------------------------------------------------

def test_restart_drops_unfinished_attempt(fillers):
    w = words("we're going to use another very popular we're going to use a very popular product.")
    cuts = disfluency.find_restart_cuts(w, fillers)
    assert len(cuts) == 1 and cuts[0].text == "we're going to use another very popular"


def test_restart_never_deletes_a_finished_sentence(fillers):
    w = words("we are going to click save. Then check it. we are going to click open.")
    assert disfluency.find_restart_cuts(w, fillers) == []


def test_restart_never_deletes_long_stretches(fillers):
    w = words("we are going to open the big red file and then click on we are going to go", step=1.5)
    assert disfluency.find_restart_cuts(w, fillers) == []


# ---- transcript parsing -----------------------------------------------------------

def test_extract_words_shifts_and_filters():
    items = [NS(word="Hello", start_offset="0.5s", end_offset="0.9s"),
             NS(word="  ", start_offset="1s", end_offset="2s"),
             NS(word="bad", start_offset="2s", end_offset="1s"),
             NS(word="late", start_offset="200s", end_offset="201s")]
    response = NS(candidates=[NS(content=NS(parts=[NS(audio_transcription=NS(words=items))]))])
    assert transcribe.extract_words(response, offset=180, duration=180) == [Word("Hello", 180.5, 180.9)]


def test_all_parts_failing_is_an_error(monkeypatch, tmp_path):
    from kv_media.ffmpeg import MediaInfo
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    info = MediaInfo(src, 400.0, 0, 48000, 1, "pcm", None, None, 0.0)
    monkeypatch.setattr(transcribe, "_make_chunk", lambda *a: None)

    def boom(*a):
        raise RuntimeError("quota")
    monkeypatch.setattr(transcribe, "_transcribe_chunk", boom)
    with pytest.raises(transcribe.TranscribeError, match="every part"):
        transcribe.transcribe(info, "fake-key")


def test_partial_failure_warns_and_is_not_cached(monkeypatch, tmp_path, capsys):
    from kv_media.ffmpeg import MediaInfo
    src = tmp_path / "a.wav"
    src.write_bytes(b"x")
    info = MediaInfo(src, 400.0, 0, 48000, 1, "pcm", None, None, 0.0)
    monkeypatch.setattr(transcribe, "_make_chunk", lambda *a: None)

    def part(client, chunk, offset, length, label):
        if offset == 180.0:
            raise RuntimeError("503")
        return [Word("hi", offset, offset + 0.3)]
    monkeypatch.setattr(transcribe, "_transcribe_chunk", part)
    result = transcribe.transcribe(info, "fake-key")
    assert [w.start for w in result] == [0.0, 360.0]
    assert "NOT transcribed" in capsys.readouterr().out
    assert transcribe.load_cached(info) is None


# ---- keys ----------------------------------------------------------------------

def test_key_from_dotenv_in_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('GEMINI_API_KEY="abc123"\n', encoding="utf-8")
    assert keys.find_key() == "abc123"


def test_environment_beats_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=file\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "env")
    assert keys.find_key() == "env"


def test_save_key_is_private(tmp_path):
    path = keys.save_key("secret")
    assert "GEMINI_API_KEY=secret" in path.read_text(encoding="utf-8")
    if os.name != "nt":
        assert (path.stat().st_mode & 0o077) == 0

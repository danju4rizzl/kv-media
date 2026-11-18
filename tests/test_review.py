"""AI review of the transcript. Gemini is never called (fake client)."""

import shutil
import subprocess
from types import SimpleNamespace as NS

import pytest

from kv_media import pipeline, report, review, transcribe
from kv_media.cli import main
from kv_media.transcribe import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg not installed")


def words_from(text: str, start: float = 0.0, step: float = 0.4) -> list[Word]:
    return [Word(w, round(start + i * step, 3), round(start + i * step + 0.3, 3)) for i, w in enumerate(text.split())]


# line 1: "I would leave the link in the in in the course material."
# line 2: "We use the blorf connectors today."
WORDS = words_from("I would leave the link in the in in the course material. We use the blorf connectors today.")


def S(line, remove, reason="repeat", conf=0.9, before=""):
    return review.Suggestion(line, remove, reason, conf, "", before)


# ---- parsing --------------------------------------------------------------------

def test_parse_suggestions_filters_and_clamps():
    raw = """```json
    {"cuts": [
      {"line": 1, "remove": " in the  in ", "reason": "Repeat", "confidence": 1.7, "why": "stutter"},
      {"line": "2", "remove": "blorf", "reason": "nonsense", "confidence": 0.6},
      {"line": 2, "remove": "today", "reason": "tangent"},
      {"line": "x", "remove": "a", "reason": "filler"},
      {"line": 3, "remove": "", "reason": "filler"},
      {"line": 4, "remove": "so so", "reason": "false start"}]}
    ```"""
    out = review.parse_suggestions(raw)
    assert [(s.line, s.remove, s.reason, s.confidence) for s in out] == [
        (1, "in the in", "repeat", 1.0), (2, "blorf", "nonsense", 0.6), (4, "so so", "false-start", 0.5)]


def test_parse_rejects_non_json():
    with pytest.raises(review.ReviewError):
        review.parse_suggestions("sorry, no")


def test_prompt_rules():
    p = review.build_prompt("[1] (00:00.0) hello")
    for needle in ("nonsense", "false-start", "repeat", "filler", "TRANSCRIPTION mistake",
                   "Never remove names", "keep the LAST", "[1] (00:00.0) hello", '"before"'):
        assert needle in p


# ---- matching ---------------------------------------------------------------------

def test_match_quote_to_word_times():
    (m,) = review.match([S(2, "blorf", "nonsense")], WORDS)
    assert (WORDS[m.first].text, m.start, m.end) == ("blorf", WORDS[15].start, WORDS[15].end)
    assert m.cut().reason == "ai-nonsense" and m.cut().text == "blorf"


def test_match_ignores_punctuation_and_case():
    (m,) = review.match([S(1, "Material", "filler")], WORDS)
    assert WORDS[m.first].text == "material."


def test_match_uses_before_to_pick_the_right_repeat():
    first = review.match([S(1, "in the")], WORDS)[0]
    assert first.first == 5                                        # first "in the" by default
    chosen = review.match([S(1, "in the", before="the in")], WORDS)[0]
    assert chosen.first == 8                                       # the one after "the in"


@pytest.mark.parametrize("bad", [
    S(1, "not in this line"), S(9, "blorf"), S(2, "blorf", before=""),
])
def test_match_skips_what_it_cannot_find(bad):
    found = review.match([bad], WORDS)
    assert (bad.remove == "blorf" and bad.line == 2) == bool(found)


def test_match_skips_too_long_and_strips_pause_marks(monkeypatch):
    monkeypatch.setattr(review, "MAX_QUOTE_WORDS", 3)
    assert review.match([S(1, "I would leave the link")], WORDS) == []
    (m,) = review.match([S(1, "in [pause 1.2s] the in")], WORDS)
    assert m.first == 5 and m.last == 7


def test_match_dedupes():
    assert len(review.match([S(2, "blorf"), S(2, "blorf", "nonsense")], WORDS)) == 1


def test_chunks_keep_header_and_split_on_lines():
    md = "# T\n\n- info\n\n---\n\n" + "".join(f"[{i}] (00:00.0) {'word ' * 20}\n\n" for i in range(1, 30))
    chunks = review._chunks(md, limit=500)
    assert len(chunks) > 1 and all(c.startswith("# T") for c in chunks)
    assert sum(c.count("] (00:00.0)") for c in chunks) == 29


# ---- Gemini call + cache ------------------------------------------------------------

REPLY = '{"cuts": [{"line": 1, "remove": "in the in", "before": "link", "reason": "repeat", "confidence": 0.95, "why": "stutter"}, {"line": 2, "remove": "blorf", "reason": "nonsense", "confidence": 0.5, "why": "unclear word"}]}'


def fake_gemini(monkeypatch, reply=REPLY):
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            return NS(text=reply)

    class Client:
        def __init__(self, api_key, **_):
            self.models = Models()

    monkeypatch.setattr("google.genai.Client", Client)
    return calls


def test_suggest_calls_once_then_uses_cache(monkeypatch):
    calls = fake_gemini(monkeypatch)
    first, cached1 = review.suggest(WORDS, 10, "k")
    second, cached2 = review.suggest(WORDS, 10, None)              # no key needed when cached
    assert calls == [review.DEFAULT_MODEL] and (cached1, cached2) == (False, True)
    assert first == second and len(first) == 2


def test_suggest_without_key_and_cache_errors():
    with pytest.raises(review.ReviewError, match="API key"):
        review.suggest(WORDS, 10, None)


def test_review_model_override(monkeypatch):
    calls = fake_gemini(monkeypatch)
    monkeypatch.setenv("GEMINI_REVIEW_MODEL", "models/cheap-one")
    review.suggest(WORDS, 10, "k")
    assert calls == ["cheap-one"]


# ---- pipeline / wizard -------------------------------------------------------------------

@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "clip.mp4"
    tone = "0.5*sin(2*PI*440*t)*(lt(t,1)+between(t,2.5,3.5)+gte(t,5.5))"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", f"aevalsrc=exprs='{tone}':s=48000:d=12",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=30:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(src)], check=True)
    return src


def _transcript(monkeypatch):
    monkeypatch.setattr(transcribe, "transcribe", lambda info, key, workers=4: WORDS)
    monkeypatch.setenv("GEMINI_API_KEY", "k")


@needs_ffmpeg
def test_plan_uses_confidence_then_explicit_approval(clip, monkeypatch):
    _transcript(monkeypatch)
    fake_gemini(monkeypatch)
    job = pipeline.Job(source=clip, remove_pauses=False, ai_review=True)
    plan = pipeline.make_plan(job)
    assert len(plan.ai_matches) == 2
    assert [c.reason for c in plan.cuts] == ["ai-repeat"]             # 0.95 >= 0.8, 0.5 is not
    job.ai_approved = {plan.ai_matches[1].key}
    plan2 = pipeline.make_plan(job)
    assert [c.reason for c in plan2.cuts] == ["ai-nonsense"]
    assert "AI suggestions  : 1 of 2 approved" in report.summary_lines(plan2)


@needs_ffmpeg
def test_review_failure_is_a_note_not_a_crash(clip, monkeypatch):
    _transcript(monkeypatch)

    class Broken:
        def __init__(self, **_):
            self.models = NS(generate_content=lambda **k: (_ for _ in ()).throw(RuntimeError("quota")))
    monkeypatch.setattr("google.genai.Client", Broken)
    plan = pipeline.make_plan(pipeline.Job(source=clip, remove_pauses=True, ai_review=True))
    assert plan.ai_matches == [] and "AI review request failed" in plan.ai_note
    assert plan.cuts                                                   # pauses still cut


@needs_ffmpeg
def test_wizard_ai_review_approve_all(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _transcript(monkeypatch)
    fake_gemini(monkeypatch)
    # file, video, AI review only, approve all, export, no AI title
    answers = iter(["1", "1", "4", "all", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    text = (tmp_path / "kvm_output" / "clip_kvm_cuts.txt").read_text(encoding="utf-8")
    assert "AI: repeats" in text and "AI: nonsense" in text and "AI suggestions (yes = cut):" in text
    assert "only approve what you really said wrong" in capsys.readouterr().out


@needs_ffmpeg
def test_direct_dry_run_lists_all_suggestions(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _transcript(monkeypatch)
    fake_gemini(monkeypatch)
    assert main([str(clip), "--ai-review", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "AI suggestions (cut when confidence >= 0.8)" in out
    assert '"blorf"' in out and '"in the in"' in out


# ---- automatic AI review with fillers / repeats ------------------------------------

def test_ai_review_switches_on_with_fillers_or_repeats():
    for kwargs in ({"remove_fillers": True}, {"remove_repeats": True}):
        job = pipeline.Job(source=None, **kwargs)
        assert job.apply_auto_ai_review() is True and job.ai_review
    job = pipeline.Job(source=None)                          # pauses only: no transcript, no AI
    assert job.apply_auto_ai_review() is False and not job.ai_review


@pytest.mark.parametrize("value", ["off", "0", "no", "FALSE"])
def test_ai_review_auto_can_be_switched_off(monkeypatch, value):
    monkeypatch.setenv("KVM_AI_REVIEW", value)
    job = pipeline.Job(source=None, remove_fillers=True)
    assert job.apply_auto_ai_review() is False and not job.ai_review


def test_cli_flags_for_auto_ai_review():
    from kv_media.cli import _job_from_args, build_parser
    def job(*argv):
        return _job_from_args(build_parser().parse_args(["x.mp4", *argv]))
    assert job("--fillers").ai_review and job("--all").ai_review and job("--repeats").ai_review
    assert not job("--fillers", "--no-ai-review").ai_review
    assert not job("--pauses").ai_review
    assert job("--pauses", "--ai-review").ai_review


@needs_ffmpeg
def test_direct_fillers_runs_ai_review_automatically(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _transcript(monkeypatch)
    fake_gemini(monkeypatch)
    assert main([str(clip), "--fillers", "--dry-run"]) == 0
    assert '"in the in"' in capsys.readouterr().out


@needs_ffmpeg
def test_wizard_adds_ai_review_when_fillers_picked(clip, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _transcript(monkeypatch)
    fake_gemini(monkeypatch)
    # file, video, pauses + fillers (AI review added), approve all, export, no AI title
    answers = iter(["1", "1", "1,2", "all", "e", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    assert main([]) == 0
    assert "AI review added automatically" in capsys.readouterr().out
    text = (tmp_path / "kvm_output" / "clip_kvm_cuts.txt").read_text(encoding="utf-8")
    assert "AI: repeats" in text


def test_ai_review_auto_off_from_env_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("KVM_AI_REVIEW=off\n", encoding="utf-8")
    job = pipeline.Job(source=None, remove_fillers=True)
    assert job.apply_auto_ai_review() is False


# ---- quotes with a word left out (seen on real recordings) ------------------------------

def test_quote_missing_a_word_still_matches():
    words = words_from("Now another thing we can do is we can we if whenever we want to sort of delete it.")
    (m,) = review.match([S(1, "we we if whenever we want to sort of", "false-start", before="do is")], words)
    assert " ".join(w.text for w in words[m.first:m.last + 1]) == "we can we if whenever we want to sort of"


def test_quote_without_the_filler_still_matches():
    words = words_from("We could do a P uh PAW is it P We could do PRW.")
    (m,) = review.match([S(1, "P PAW is it P", "nonsense")], words)
    assert " ".join(w.text for w in words[m.first:m.last + 1]) == "P uh PAW is it P"


def test_fuzzy_match_is_limited():
    words = words_from("we can really truly honestly do it now.")
    assert review.match([S(1, "we can do it", "repeat")], words) == []          # 3 words missing
    assert review.match([S(1, "we do", "repeat")], words) == []                 # short quotes: exact only

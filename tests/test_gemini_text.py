"""Busy / missing Gemini text models fall back to the next model. Gemini is never called."""

from types import SimpleNamespace as NS

import pytest

from kv_media import gemini_text, review, titles
from kv_media.transcribe import Word

GOOD = '{"titles": [{"title": "A Real Title That Works", "style": "benefit"}]}'


class ApiError(Exception):
    def __init__(self, code, status=""):
        super().__init__(f"{code} {status}")
        self.code, self.status = code, status


def fake_client(monkeypatch, behaviour):
    """behaviour: model -> list of results per call (an exception or reply text)."""
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            queue = behaviour.get(model) or [ApiError(404, "NOT_FOUND")]
            result = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(result, Exception):
                raise result
            return NS(text=result)

    class Client:
        def __init__(self, **_):
            self.models = Models()

    monkeypatch.setattr("google.genai.Client", Client)
    monkeypatch.setattr(gemini_text.time, "sleep", lambda s: None)
    return calls


WORDS = [Word(w, i, i + 0.5) for i, w in enumerate("one two three four five six".split())]


def test_chain_dedupes_and_strips_prefix():
    assert gemini_text.chain("models/a", ("b", "a", " c ")) == ["a", "b", "c"]


FIRST, SECOND, THIRD = titles.DEFAULT_MODEL, *titles.FALLBACK_MODELS[:2]


def test_defaults_are_cheap_and_long_available():
    assert titles.DEFAULT_MODEL == review.DEFAULT_MODEL == "gemini-2.5-flash-lite"


def test_busy_model_is_skipped_at_once(monkeypatch, capsys):
    calls = fake_client(monkeypatch, {FIRST: [ApiError(503, "UNAVAILABLE")], SECOND: [GOOD]})
    pack = titles.generate(WORDS, 60, True, "k")
    assert pack.titles[0].text == "A Real Title That Works" and pack.model == SECOND
    assert calls == [FIRST, SECOND]
    assert f"trying {SECOND}" in capsys.readouterr().out


def test_all_busy_waits_once_then_succeeds(monkeypatch, capsys):
    waits = []
    calls = fake_client(monkeypatch, {FIRST: [ApiError(503), GOOD]})
    monkeypatch.setattr(gemini_text.time, "sleep", waits.append)
    assert titles.generate(WORDS, 60, True, "k").model == FIRST
    assert waits == list(gemini_text.ROUND_DELAYS) and calls.count(FIRST) == 2
    assert "every model is busy" in capsys.readouterr().out


def test_missing_model_and_bad_json_are_skipped(monkeypatch):
    calls = fake_client(monkeypatch, {FIRST: [ApiError(404)], SECOND: ["not json"], THIRD: [GOOD]})
    assert titles.generate(WORDS, 60, True, "k").model == THIRD
    assert calls == [FIRST, SECOND, THIRD]


def test_all_busy_gives_one_clear_error(monkeypatch):
    fake_client(monkeypatch, {m: [ApiError(503, "UNAVAILABLE")]
                              for m in gemini_text.chain(titles.DEFAULT_MODEL, titles.FALLBACK_MODELS)})
    with pytest.raises(titles.TitleError, match="every model failed.*503 UNAVAILABLE.*try again"):
        titles.generate(WORDS, 60, True, "k")


def test_other_errors_are_not_hidden(monkeypatch):
    fake_client(monkeypatch, {FIRST: [ApiError(401, "UNAUTHENTICATED")]})
    with pytest.raises(titles.TitleError, match="401"):
        titles.generate(WORDS, 60, True, "k")


def test_review_falls_back_too(monkeypatch):
    reply = '{"cuts": [{"line": 1, "remove": "two", "reason": "filler", "confidence": 0.9}]}'
    calls = fake_client(monkeypatch, {review.DEFAULT_MODEL: [ApiError(429, "RESOURCE_EXHAUSTED")],
                                      review.FALLBACK_MODELS[0]: [reply]})
    suggestions, cached = review.suggest(WORDS, 10, "k")
    assert [s.remove for s in suggestions] == ["two"] and not cached
    assert calls == [review.DEFAULT_MODEL, review.FALLBACK_MODELS[0]]

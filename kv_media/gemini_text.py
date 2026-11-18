"""Gemini text requests (titles, AI review) that survive a busy model.

The newest Flash models are often "experiencing high demand" (503) or rate limited (429).
Waiting minutes on one model is the wrong fix: other text models do these small jobs just as
well. So a busy model is skipped at once and the next model in the chain is tried; only when
every model is busy does kvm wait once and go round again. A model that does not exist for this
key (404) or returns unusable JSON is skipped the same way.

Transcription does NOT use this: it needs the one model that returns word timestamps.
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from kv_media import ui

T = TypeVar("T")

BUSY_CODES = (429, 500, 503, 504)          # try again / try another model
SKIP_CODES = (400, 403, 404)               # this model is not usable with this key: try another
ROUND_DELAYS = (10,)                       # wait before another round when every model was busy


class AllModelsFailed(RuntimeError):
    pass


def chain(primary: str, fallbacks: tuple[str, ...]) -> list[str]:
    """``primary`` then the fallbacks, without duplicates."""
    out: list[str] = []
    for model in (primary, *fallbacks):
        model = model.strip().removeprefix("models/")
        if model and model not in out:
            out.append(model)
    return out


def _short(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if code:
        return f"{code} {status or ''}".strip()
    return str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__


def generate_json(client, models: list[str], prompt: str, temperature: float, label: str,
                  parse: Callable[[str], T]) -> tuple[T, str]:
    """Ask each model in turn until one gives a reply ``parse`` accepts. Returns (parsed, model)."""
    from google.genai import types

    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature)
    problems: list[str] = []
    for round_no in range(len(ROUND_DELAYS) + 1):
        problems, any_busy = [], False
        for n, model in enumerate(models):
            nxt = models[n + 1] if n + 1 < len(models) else None
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=config)
            except Exception as exc:  # API / network errors: show the message, never the key
                code = getattr(exc, "code", None)
                if code not in BUSY_CODES and code not in SKIP_CODES:
                    raise
                any_busy = any_busy or code in BUSY_CODES
                problems.append(f"{model}: {_short(exc)}")
                if nxt:
                    ui.warn(f"{label}: {model} unavailable ({_short(exc)}), trying {nxt}")
                continue
            try:
                return parse(getattr(response, "text", "") or ""), model
            except Exception as exc:
                problems.append(f"{model}: unusable reply ({exc})")
                if nxt:
                    ui.warn(f"{label}: {model} gave an unusable reply, trying {nxt}")
        if not any_busy or round_no == len(ROUND_DELAYS):
            break
        delay = ROUND_DELAYS[round_no]
        ui.warn(f"{label}: every model is busy, trying again in {delay}s")
        time.sleep(delay)
    raise AllModelsFailed("every model failed - " + "; ".join(problems) +
                          ". Google's servers are busy; try again in a few minutes.")

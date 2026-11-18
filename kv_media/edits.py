"""Cut by editing the transcript: strike words out in <name>_transcript.md and kvm cuts them.

    [12] (00:51.3) so what we're ~~going to [pause 1.4s] so~~ today we set up the connectors

- Strike with ``~~ ~~`` (Markdown strikethrough). A strike may cover a few words, a whole line
  (``~~[12] (00:51.3) ...~~`` or just the words) or run over several lines.
- Each line is mapped back to the exact words of the saved transcript by its number, so a word
  said twice in a line is cut where YOU struck it. If you also changed the text of a line
  (fixed a typo), the struck words are found by quoting them instead, like the AI review.
- Free and local: no Gemini call (the words come from the saved transcript).
- kvm never overwrites a transcript that has strikes in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kv_media import review, transcript_md
from kv_media.cuts import Cut
from kv_media.transcribe import Word

STRIKE = "~~"
_LINE = re.compile(r"^\s*(~~)?\s*\[(\d+)\]\s*\(([\d:.]+)\)\s?(.*)$")
_PAUSE = re.compile(r"\*?\[pause [^\]]*\]\*?")
_TOKEN = re.compile(r"~~|(?:[^\s~]|~(?!~))+")
_SOURCE = re.compile(r"^- Source: `(.+)`\s*$", re.M)


class EditError(RuntimeError):
    pass


@dataclass
class EditResult:
    cuts: list[Cut]
    struck_words: int
    lines: int                  # lines with strikes
    not_found: list[str]        # struck text that could not be found in the transcript


def has_strikes(text: str) -> bool:
    return any(_LINE.match(line) and STRIKE in line for line in text.splitlines())


def source_name(text: str) -> str | None:
    m = _SOURCE.search(text)
    return m.group(1) if m else None


def _norm(token: str) -> str:
    return re.sub(r"[^\w']+", "", token.lower())


def parse_lines(text: str) -> list[tuple[int, list[tuple[str, bool]]]]:
    """[(line number, [(word, struck), ...])] for every transcript line. A strike left open at the
    end of a line carries on into the next line (strikes over several lines)."""
    out = []
    struck = False
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        if m.group(1):
            struck = not struck
        tokens: list[tuple[str, bool]] = []
        for tok in _TOKEN.findall(_PAUSE.sub(" ", m.group(4))):
            if tok == STRIKE:
                struck = not struck
            elif _norm(tok):
                tokens.append((tok, struck))
        out.append((int(m.group(2)), tokens))
    if struck:
        raise EditError("A ~~ strike is opened but never closed. Close it with ~~ after the last "
                        "word to cut, otherwise everything after it would be cut.")
    return out


def _runs(indices: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in sorted(set(indices)):
        if runs and i == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def edit_cuts(text: str, words: list[Word]) -> EditResult:
    """Cuts for every struck word. Consecutive struck words (also across lines) become one cut."""
    lines = {l.number: l for l in transcript_md.split_lines(words)}
    norm = [_norm(w.text) for w in words]
    struck_idx: list[int] = []
    not_found: list[str] = []
    struck_lines = 0
    for number, tokens in parse_lines(text):
        if not any(s for _, s in tokens):
            continue
        struck_lines += 1
        line = lines.get(number)
        if line is None:
            not_found.append(f"line {number}: no such line in the saved transcript")
            continue
        expected = norm[line.first:line.last + 1]
        if [_norm(w) for w, _ in tokens] == expected:              # text unchanged: exact positions
            struck_idx += [line.first + k for k, (_, s) in enumerate(tokens) if s]
            continue
        # The line text was edited: find each struck run by quoting it.
        k = 0
        while k < len(tokens):
            if not tokens[k][1]:
                k += 1
                continue
            j = k
            while j + 1 < len(tokens) and tokens[j + 1][1]:
                j += 1
            quote = " ".join(w for w, _ in tokens[k:j + 1])
            before = " ".join(w for w, _ in tokens[max(0, k - 3):k])
            hit = _find(quote, before, number, words)
            if hit is None:
                not_found.append(f"line {number}: \"{quote}\"")
            else:
                struck_idx += list(range(hit[0], hit[1] + 1))
            k = j + 1
    cuts = [Cut(words[i].start, words[j].end, "edit", " ".join(w.text for w in words[i:j + 1]))
            for i, j in _runs(struck_idx)]
    return EditResult(cuts, len(set(struck_idx)), struck_lines, not_found)


def _find(quote: str, before: str, number: int, words: list[Word]) -> tuple[int, int] | None:
    s = review.Suggestion(number, quote, "edit", 1.0, "", before)
    # your own strikes are not limited in length like AI suggestions are
    found = review.match([s], words, max_words=None, max_seconds=None)
    return (found[0].first, found[0].last) if found else None


def load(path: Path, words: list[Word]) -> EditResult:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EditError(f"Cannot read {path}: {exc}") from exc
    if not has_strikes(text):
        raise EditError(f"{path.name} has no struck words (~~like this~~).")
    return edit_cuts(text, words)


def find_edited(source: Path, folders: list[Path]) -> Path | None:
    """The newest *_transcript.md in ``folders`` that belongs to ``source`` and has strikes."""
    best: tuple[float, Path] | None = None
    for folder in folders:
        try:
            candidates = list(folder.glob("*_transcript.md")) + list(folder.glob("*/*_transcript.md"))
        except OSError:
            continue
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8-sig")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if source_name(text) == source.name and has_strikes(text):
                if best is None or mtime > best[0]:
                    best = (mtime, path)
    return best[1] if best else None

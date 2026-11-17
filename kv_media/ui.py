"""Small terminal helpers: colours, prompts, progress bar. No third-party dependencies."""

from __future__ import annotations

import os
import sys

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str: return _wrap("1", t)
def dim(t: str) -> str: return _wrap("2", t)
def green(t: str) -> str: return _wrap("32", t)
def yellow(t: str) -> str: return _wrap("33", t)
def red(t: str) -> str: return _wrap("31", t)
def cyan(t: str) -> str: return _wrap("36", t)


def step(number: int, title: str) -> None:
    print(f"\n{cyan(f'Step {number}')}  {bold(title)}")


def info(text: str) -> None:
    print(f"  {text}")


def warn(text: str) -> None:
    print(f"  {yellow('!')} {text}")


def error(text: str) -> None:
    print(f"{red('Error:')} {text}", file=sys.stderr)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" {dim(f'[{default}]')}" if default else ""
    answer = input(f"  {prompt}{suffix}: ").strip()
    return answer or default


def choose(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    """Numbered menu; returns the chosen key."""
    for key, label in options:
        print(f"    {bold(key)}) {label}")
    keys = {k for k, _ in options}
    while True:
        answer = ask(prompt, default).lower()
        if answer in keys:
            return answer
        warn(f"Please enter one of: {', '.join(k for k, _ in options)}")


def progress(fraction: float, width: int = 30) -> None:
    filled = int(width * fraction)
    bar = "#" * filled + "-" * (width - filled)
    end = "\n" if fraction >= 1.0 else ""
    sys.stdout.write(f"\r  [{bar}] {fraction:5.1%}{end}")
    sys.stdout.flush()

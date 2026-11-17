"""Gemini API key lookup. Never prints, logs or sends the key anywhere but Gemini.

Order: GEMINI_API_KEY environment variable -> .env in the current folder -> .env in the
kv-media project folder -> ~/.config/kvm/.env -> ask (hidden input, optionally saved there).
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path

KEY_NAME = "GEMINI_API_KEY"
CONFIG_ENV = Path.home() / ".config" / "kvm" / ".env"
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and value:
            values[key] = value
    return values


def load_env_files() -> None:
    """Load .env files into os.environ without overriding real environment variables."""
    for path in (Path.cwd() / ".env", PROJECT_ENV, CONFIG_ENV):
        for key, value in _read_env(path).items():
            os.environ.setdefault(key, value)


def find_key() -> str | None:
    load_env_files()
    return os.environ.get(KEY_NAME) or None


def save_key(key: str) -> Path:
    CONFIG_ENV.parent.mkdir(parents=True, exist_ok=True)
    existing = {k: v for k, v in _read_env(CONFIG_ENV).items() if k != KEY_NAME}
    lines = [f"{k}={v}" for k, v in existing.items()] + [f"{KEY_NAME}={key}"]
    CONFIG_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        CONFIG_ENV.chmod(0o600)
    except OSError:
        pass
    return CONFIG_ENV


def ask_for_key() -> str | None:
    """Hidden prompt. Returns None if the user gives nothing."""
    from kv_media import ui

    ui.info("Filler and repeat removal use Gemini to hear the words. A Gemini API key is needed")
    ui.info(ui.dim("(free from https://aistudio.google.com/apikey). Input is hidden."))
    key = getpass.getpass("  Gemini API key: ").strip()
    if not key:
        return None
    os.environ[KEY_NAME] = key
    if ui.ask(f"Save it to {CONFIG_ENV} for next time? (y/n)", "y").lower().startswith("y"):
        ui.info(f"Saved to {save_key(key)}")
    return key

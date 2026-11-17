import pytest

from kv_media import keys, transcribe


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    """Tests never see the real API key, transcript cache or saved config."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr(transcribe, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(keys, "CONFIG_ENV", home / "config" / ".env")
    monkeypatch.setattr(keys, "PROJECT_ENV", home / "project.env")

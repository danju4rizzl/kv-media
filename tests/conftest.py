import pytest

from kv_media import keys, transcribe


@pytest.fixture(autouse=True)
def isolate(tmp_path_factory, monkeypatch):
    """Tests never see the real API key, transcript cache or saved config."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_TITLE_MODEL", raising=False)
    monkeypatch.setattr(transcribe, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(transcribe, "shared_cache_dirs", lambda: [])
    monkeypatch.delenv("KVM_CACHE_DIR", raising=False)
    monkeypatch.delenv("KVM_AI_REVIEW", raising=False)
    monkeypatch.setattr(keys, "CONFIG_ENV", home / "config" / ".env")
    monkeypatch.setattr(keys, "PROJECT_ENV", home / "project.env")


@pytest.fixture(autouse=True)
def no_real_gemini(monkeypatch):
    """Hard guard: a test can never reach the real Gemini API (and spend credits).
    Tests that need a client replace google.genai.Client themselves."""
    class _Blocked:
        def __getattr__(self, name):
            raise RuntimeError(f"Real Gemini call attempted in a test ({name}) - mock it instead.")

    class _OfflineClient:
        """Can be created (no network), but any request (models / files / ...) fails."""
        def __init__(self, *_args, **_kwargs):
            self.models = self.files = self.aio = self.chats = _Blocked()

    monkeypatch.setattr("google.genai.Client", _OfflineClient)

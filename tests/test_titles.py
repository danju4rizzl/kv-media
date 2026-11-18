"""AI title parsing, cleaning, filenames and the SEO pack. Gemini is never called."""

from types import SimpleNamespace as NS

import pytest

from kv_media import titles
from kv_media.transcribe import Word


def test_parse_pack_full():
    pack = titles.parse_pack("""```json
    {"titles": [{"title": "\\"5 Editing Mistakes Killing Your Views\\" 🔥", "style": "List", "why": "numbers"},
                {"title": "5 editing mistakes killing your views", "style": "dup"},
                "How to Edit Faster Without Losing Quality #editing",
                {"title": "Short", "style": "too short"}],
     "keyword": " video editing ", "description": "Learn  fast\\nediting.",
     "tags": ["editing", "editing", " "], "hashtags": ["#Editing", "video tips", "#editing"]}
    ```""")
    assert [t.text for t in pack.titles] == ["5 Editing Mistakes Killing Your Views",
                                            "How to Edit Faster Without Losing Quality"]
    assert pack.titles[0].style == "list" and pack.titles[1].style == "title"
    assert pack.keyword == "video editing" and pack.description == "Learn fast editing."
    assert pack.tags == ["editing"] and pack.hashtags == ["#Editing", "#videotips"]


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"titles": []}', '{"titles": [{"title": ""}]}'])
def test_parse_pack_rejects_bad_output(raw):
    with pytest.raises(titles.TitleError):
        titles.parse_pack(raw)


def test_clean_title_caps_length_on_a_word():
    long = "How to Remove Filler Words From Every Single Recording You Ever Make Forever And Ever"
    cleaned = titles.clean_title(long)
    assert len(cleaned) <= titles.TITLE_MAX and long.startswith(cleaned) and not cleaned.endswith(" ")


@pytest.mark.parametrize("title, slug", [
    ("How I Cut 40% of Dead Air (in 5 Min)", "how-i-cut-40-of-dead-air-in-5-min"),
    ("Café & Crème: Don't Panic!", "cafe-and-creme-dont-panic"),
    ('Bad <chars> "here" / or \\ there? *|:', "bad-chars-here-or-there"),
    ("snake_case_title", "snake-case-title"),
    ("!!!", "untitled"),
])
def test_filename_slug(title, slug):
    assert titles.filename_slug(title) == slug


def test_filename_slug_length_ends_on_a_word():
    slug = titles.filename_slug("word " * 40, limit=30)
    assert len(slug) <= 30 and not slug.endswith("-") and slug.split("-")[-1] == "word"


def test_unique_path(tmp_path):
    first = tmp_path / "title.mp4"
    assert titles.unique_path(first) == first
    first.write_bytes(b"x")
    (tmp_path / "title-2.mp4").write_bytes(b"x")
    assert titles.unique_path(first) == tmp_path / "title-3.mp4"
    assert titles.unique_path(first, keep=first) == first


def test_unique_pack_dir(tmp_path):
    first = titles.unique_pack_dir(tmp_path, "my-pack")
    assert first == tmp_path / "my-pack"
    first.mkdir()
    assert titles.unique_pack_dir(tmp_path, "my-pack") == tmp_path / "my-pack-2"
    assert titles.unique_pack_dir(tmp_path, "my-pack", keep=first) == first


def test_apply_title_creates_pack_folder(tmp_path):
    from fractions import Fraction
    from kv_media import ffmpeg, pipeline
    job = pipeline.Job(source=tmp_path / "input.mp4")
    info = ffmpeg.MediaInfo(tmp_path / "input.mp4", 10.0, 0, 44100, 2, "aac", 1, Fraction(30, 1), 0.0)
    plan = pipeline.Plan(job, info, [], [], tmp_path / "kvm_output" / "input_kvm.mp4")
    pack = titles.TitlePack(titles=[titles.Title("How to Master AI Terminal Commands", "how-to")])
    pipeline.apply_title(plan, pack.titles[0].text, pack)

    expected_dir = tmp_path / "kvm_output" / "how-to-master-ai-terminal-commands"
    assert plan.output == expected_dir / "how-to-master-ai-terminal-commands.mp4"
    assert plan.seo_path == expected_dir / "how-to-master-ai-terminal-commands_seo.txt"
    assert plan.transcript_path == expected_dir / "how-to-master-ai-terminal-commands_transcript.md"
    assert plan.report_path == expected_dir / "how-to-master-ai-terminal-commands_cuts.txt"

    # Calling apply_title again with a new title does NOT nest folders
    pipeline.apply_title(plan, "A New Better Title", pack)
    new_dir = tmp_path / "kvm_output" / "a-new-better-title"
    assert plan.output == new_dir / "a-new-better-title.mp4"


def test_apply_title_handles_folder_collision(tmp_path):
    from fractions import Fraction
    from kv_media import ffmpeg, pipeline
    job = pipeline.Job(source=tmp_path / "input.mp4")
    info = ffmpeg.MediaInfo(tmp_path / "input.mp4", 10.0, 0, 44100, 2, "aac", 1, Fraction(30, 1), 0.0)
    plan = pipeline.Plan(job, info, [], [], tmp_path / "kvm_output" / "input_kvm.mp4")
    pack = titles.TitlePack(titles=[titles.Title("Video Title", "how-to")])

    existing_dir = tmp_path / "kvm_output" / "video-title"
    existing_dir.mkdir(parents=True)

    pipeline.apply_title(plan, pack.titles[0].text, pack)
    assert plan.output == tmp_path / "kvm_output" / "video-title-2" / "video-title-2.mp4"


def test_apply_title_respects_no_pack_dir(tmp_path):
    from fractions import Fraction
    from kv_media import ffmpeg, pipeline
    job = pipeline.Job(source=tmp_path / "input.mp4", pack_dir=False)
    info = ffmpeg.MediaInfo(tmp_path / "input.mp4", 10.0, 0, 44100, 2, "aac", 1, Fraction(30, 1), 0.0)
    plan = pipeline.Plan(job, info, [], [], tmp_path / "kvm_output" / "input_kvm.mp4")
    pack = titles.TitlePack(titles=[titles.Title("How to Master AI Terminal Commands", "how-to")])
    pipeline.apply_title(plan, pack.titles[0].text, pack)

    # When pack_dir is False, files are written flat in the output folder
    assert plan.output == tmp_path / "kvm_output" / "how-to-master-ai-terminal-commands.mp4"
    assert plan.seo_path == tmp_path / "kvm_output" / "how-to-master-ai-terminal-commands_seo.txt"
    assert plan.transcript_path == tmp_path / "kvm_output" / "how-to-master-ai-terminal-commands_transcript.md"
    assert plan.report_path == tmp_path / "kvm_output" / "how-to-master-ai-terminal-commands_cuts.txt"


def test_pack_dir_auto_env(monkeypatch):
    from kv_media import pipeline
    monkeypatch.delenv("KVM_PACK_DIR", raising=False)
    assert pipeline.pack_dir_auto() is True
    for val in ("off", "0", "false", "no", "OFF", "False"):
        monkeypatch.setenv("KVM_PACK_DIR", val)
        assert pipeline.pack_dir_auto() is False
    for val in ("on", "1", "true", "yes", "ON", "True"):
        monkeypatch.setenv("KVM_PACK_DIR", val)
        assert pipeline.pack_dir_auto() is True


def test_transcript_text_keeps_start_and_end():
    words = [Word(f"w{i}", i, i + 0.5) for i in range(5000)]
    text = titles.transcript_text(words, limit=2000)
    assert text.startswith("w0 w1") and text.endswith("w4999") and "omitted" in text
    assert len(text) < 2100


def test_prompt_has_the_rules_and_rejected_titles():
    prompt = titles.build_prompt("hello there everyone", 610, True, avoid=["Old Idea One"])
    for needle in ("first 40 characters", "Never more than 60", "HONEST", "curiosity", "how-to",
                   "list", "benefit", "bold-claim", "10-minute video", "- Old Idea One", "hello there"):
        assert needle in prompt
    assert "audio recording" in titles.build_prompt("x", 30, False)


def test_generate_uses_title_model_and_json(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.update(model=model, json=config.response_mime_type, prompt=contents)
            return NS(text='{"titles": [{"title": "A Real Title That Works", "style": "benefit"}]}')

    class FakeClient:
        def __init__(self, api_key, **_):
            seen["key"] = api_key
            self.models = FakeModels()

    monkeypatch.setattr("google.genai.Client", FakeClient)
    monkeypatch.setenv("GEMINI_TITLE_MODEL", "models/some-model")
    pack = titles.generate([Word(w, 0, 1) for w in "one two three four five six".split()], 60, True, "k")
    assert pack.titles[0].text == "A Real Title That Works"
    assert seen["model"] == "some-model" and seen["json"] == "application/json" and seen["key"] == "k"


def test_generate_refuses_tiny_transcript():
    with pytest.raises(titles.TitleError, match="too short"):
        titles.generate([Word("hi", 0, 1)], 5, True, "k")


def test_seo_text():
    pack = titles.TitlePack([titles.Title("Chosen One Title", "how-to"), titles.Title("Other Idea Here", "list")],
                            keyword="kw", description="Desc.", tags=["a", "b"], hashtags=["#a"])
    text = titles.build_seo_text(pack, "Chosen One Title")
    assert "Chosen One Title  (16 chars)" in text and "a, b" in text and "#a" in text
    assert "[list] Other Idea Here" in text and "[how-to]" not in text

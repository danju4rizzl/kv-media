from fractions import Fraction
from pathlib import Path

import pytest

from kv_media import cuts, paths, pauses, render
from kv_media.cuts import Cut
from kv_media.ffmpeg import MediaInfo, parse_probe


# ---- paths -----------------------------------------------------------------

@pytest.mark.parametrize("raw", ['"{p}"', "'{p}'", "& '{p}'", "  {p}  "])
def test_clean_path_strips_quotes_and_powershell(tmp_path, raw):
    f = tmp_path / "my talk.mp4"
    f.write_bytes(b"")
    assert paths.clean_path(raw.format(p=f)) == f


@pytest.mark.skipif(paths.os.name == "nt", reason="backslash is the separator on Windows")
def test_clean_path_bash_escaped_spaces(tmp_path):
    f = tmp_path / "my talk.mp4"
    f.write_bytes(b"")
    assert paths.clean_path(str(f).replace(" ", "\\ ")) == f


def test_windows_path_converted_for_wsl(monkeypatch):
    monkeypatch.setattr(paths.os, "name", "posix")
    options = paths._candidates(r"C:\Users\pc\Videos\a b.mp4")
    assert "/mnt/c/Users/pc/Videos/a b.mp4" in options


@pytest.mark.parametrize("raw", [r"/c/Users/pc/My\ Videos/a.mp4", "/mnt/c/Users/pc/My Videos/a.mp4"])
def test_git_bash_and_wsl_paths_converted_on_windows(monkeypatch, raw):
    monkeypatch.setattr(paths.os, "name", "nt")
    assert "C:/Users/pc/My Videos/a.mp4" in paths._candidates(raw)


def test_output_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = Path("/x/lecture.mkv")
    assert paths.output_path(src, False, None, None) == tmp_path / "kvm_output" / "lecture_kvm.mkv"
    assert paths.output_path(Path("/x/a.avi"), False, None, None).suffix == ".mp4"
    assert paths.output_path(src, True, None, None).suffix == ".wav"
    assert paths.output_path(src, True, "mp3", None).name == "lecture_kvm.mp3"
    assert paths.output_path(src, False, None, "out/") == tmp_path / "out" / "lecture_kvm.mkv"
    assert paths.output_path(src, False, None, "out/x.mp4") == tmp_path / "out" / "x.mp4"
    assert paths.report_path(tmp_path / "a_kvm.mp4").name == "a_kvm_cuts.txt"


def test_list_media_skips_previous_results(tmp_path):
    for name in ("b.mp4", "a.WAV", "a_kvm.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in paths.list_media(tmp_path)] == ["a.WAV", "b.mp4"]


# ---- pauses ----------------------------------------------------------------

LOG = """
[silencedetect @ 0x1] silence_start: 1.002
[silencedetect @ 0x1] silence_end: 2.498 | silence_duration: 1.496
[silencedetect @ 0x1] silence_start: -0.01
[silencedetect @ 0x1] silence_end: 0.9 | silence_duration: 0.91
[silencedetect @ 0x1] silence_start: 9.0
"""


def test_parse_silencedetect_closes_trailing_silence():
    assert pauses.parse_silencedetect(LOG, 10.0) == [(1.002, 2.498), (0.0, 0.9), (9.0, 10.0)]


def test_silences_to_cuts_keeps_padding_but_not_at_file_edges():
    result = pauses.silences_to_cuts([(0.0, 1.0), (3.0, 4.0), (3.0, 3.35), (9.0, 10.0)], 10.0, 0.2)
    assert result == [Cut(0.0, 0.8), Cut(3.2, 3.8), Cut(9.2, 10.0)]


# ---- cuts ------------------------------------------------------------------

def test_normalize_merges_overlaps_and_slivers():
    raw = [Cut(5, 6, "pause"), Cut(1, 2, "pause"), Cut(1.5, 2.5, "filler"), Cut(2.55, 3, "pause")]
    assert cuts.normalize(raw, 10) == [Cut(1, 3, "pause+filler"), Cut(5, 6, "pause")]


def test_normalize_absorbs_edge_slivers_and_clamps():
    assert cuts.normalize([Cut(0.05, 1), Cut(9, 12)], 10) == [Cut(0.0, 1), Cut(9, 10)]


def test_keep_segments():
    assert cuts.keep_segments([Cut(0, 1), Cut(4, 5)], 10) == [(1, 4), (5, 10)]


def test_check_plan_rejects_removing_almost_everything():
    with pytest.raises(cuts.CutPlanError):
        cuts.check_plan([Cut(0, 9)], 10)
    cuts.check_plan([Cut(0, 5)], 10)


# ---- render ----------------------------------------------------------------

def _info(fps="30/1", offset=0.0, duration=10.0):
    return MediaInfo(Path("in.mp4"), duration, 1, 48000, 2, "aac", 0, Fraction(fps), offset)


def test_segments_snap_to_frames():
    segs = render.plan_segments([(0, 1.01), (2.49, 10)], _info(), with_video=True)
    assert [(s.frame_start, s.frame_end) for s in segs] == [(0, 30), (75, 300)]
    assert segs[0].end == pytest.approx(1.0) and segs[1].start == pytest.approx(2.5)


def test_segments_without_video_are_untouched():
    segs = render.plan_segments([(0, 1.01)], _info(), with_video=False)
    assert (segs[0].start, segs[0].end, segs[0].frame_start) == (0, 1.01, None)


def test_audio_graph_crossfades_with_handles():
    segs = render.plan_segments([(0, 1), (2, 3), (5, 10)], _info(), with_video=True)
    graph = render.build_audio_graph(segs, _info())
    assert "asplit=3" in graph
    assert graph.count("acrossfade=d=0.03") == 2
    # middle piece borrows 15 ms on both sides; edges do not
    assert "atrim=start=0.000000:end=1.015000" in graph
    assert "atrim=start=1.985000:end=3.015000" in graph
    assert "atrim=start=4.985000:end=10.000000" in graph


def test_video_graph_frame_exact():
    segs = render.plan_segments([(0, 1), (2, 3), (5, 10)], _info(), with_video=True)
    graph = render.build_video_graph(segs, _info())
    assert "fps=30,split=3" in graph
    assert "trim=start_frame=60:end_frame=90" in graph
    assert "concat=n=3:v=1:a=0[vout]" in graph


def test_audio_graph_single_segment():
    graph = render.build_audio_graph([render.Segment(0.5, 9.5)], _info())
    assert graph == "[0:1]atrim=start=0.500000:end=9.500000,asetpts=PTS-STARTPTS[aout]"


def test_parse_probe_skips_cover_art():
    data = {"format": {"duration": "12.5", "start_time": "0"},
            "streams": [{"index": 0, "codec_type": "video", "disposition": {"attached_pic": 1}},
                        {"index": 1, "codec_type": "audio", "sample_rate": "44100", "channels": 2,
                         "codec_name": "mp3"}]}
    info = parse_probe(Path("a.mp3"), data)
    assert not info.has_video and info.audio_index == 1 and info.sample_rate == 44100

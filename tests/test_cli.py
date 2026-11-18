import pytest

from kv_media import __version__
from kv_media.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_pauses_without_file_is_an_error(capsys):
    assert main(["--pauses"]) == 2


def test_wizard_quit_with_ctrl_c(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def interrupt(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert main([]) == 130


def test_pack_dir_flags():
    from kv_media.cli import build_parser, _job_from_args
    parser = build_parser()

    args_default = parser.parse_args(["video.mp4", "--pauses"])
    assert args_default.pack_dir is None
    job_default = _job_from_args(args_default)
    assert job_default.pack_dir is True

    args_no_pack = parser.parse_args(["video.mp4", "--pauses", "--no-pack-dir"])
    assert args_no_pack.pack_dir is False
    job_no_pack = _job_from_args(args_no_pack)
    assert job_no_pack.pack_dir is False

    args_pack = parser.parse_args(["video.mp4", "--pauses", "--pack-dir"])
    assert args_pack.pack_dir is True
    job_pack = _job_from_args(args_pack)
    assert job_pack.pack_dir is True

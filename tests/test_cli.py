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

from pathlib import Path
from types import SimpleNamespace

import pytest

from app import system_open


def test_windows_directory_opens_new_explorer_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    folder = tmp_path / "downloads"
    folder.mkdir()

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=123),
    )
    monkeypatch.setattr(
        system_open.os,
        "startfile",
        lambda _target: pytest.fail("directories should use explorer.exe, not os.startfile"),
        raising=False,
    )

    system_open.open_path_with_default_app(folder)

    assert opened == [["explorer.exe", "/n,", str(folder)]]


def test_windows_file_uses_default_file_association(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[str] = []
    video = tmp_path / "video.mp4"
    video.write_text("video", encoding="utf-8")

    monkeypatch.setattr(system_open.os, "name", "nt")
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda _command, **_kwargs: pytest.fail("files should use the default file association"),
    )
    monkeypatch.setattr(system_open.os, "startfile", opened.append, raising=False)

    system_open.open_path_with_default_app(video)

    assert opened == [str(video)]

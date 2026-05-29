from pathlib import Path
from types import SimpleNamespace

import pytest

from app import system_open


def test_windows_directory_opens_new_explorer_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    raised: list[tuple[int | None, str | None]] = []
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
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: raised.append((pid, title)))

    system_open.open_path_with_default_app(folder)

    assert opened == [["explorer.exe", "/n,", str(folder)]]
    assert raised == [(123, "downloads")]


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


def test_open_video_uses_robust_player_when_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    opened: list[list[str]] = []
    raised: list[tuple[int | None, str | None]] = []
    video = tmp_path / "video.webm"
    video.write_text("video", encoding="utf-8")

    monkeypatch.setattr(
        system_open,
        "_video_player_candidates",
        lambda: [system_open.VideoPlayerCandidate("VLC media player", ["vlc"], robust=True)],
    )
    monkeypatch.setattr(
        system_open.subprocess,
        "Popen",
        lambda command, **_kwargs: opened.append(command) or SimpleNamespace(pid=456),
    )
    monkeypatch.setattr(system_open, "_bring_windows_window_to_front", lambda pid, title: raised.append((pid, title)))

    system_open.open_video_with_best_player(video, "webm 路 vp9 + opus")

    assert opened == [["vlc", str(video)]]
    assert raised == [(456, "video.webm")]


def test_open_video_reports_missing_capable_player(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "video.webm"
    video.write_text("video", encoding="utf-8")
    monkeypatch.setattr(system_open, "_video_player_candidates", lambda: [])

    with pytest.raises(system_open.LocalOpenError) as exc_info:
        system_open.open_video_with_best_player(video, "webm 路 vp9 + opus")

    message = str(exc_info.value)
    assert "当前视频格式：webm 路 vp9 + opus" in message
    assert "VLC media player" in message
    assert "mpv" in message

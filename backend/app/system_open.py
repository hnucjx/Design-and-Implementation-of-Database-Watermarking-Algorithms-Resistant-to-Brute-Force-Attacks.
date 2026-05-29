import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


class LocalOpenError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoPlayerCandidate:
    name: str
    command: list[str]
    robust: bool


def open_path_with_default_app(path: Path) -> None:
    target = str(path)
    if os.name == "nt":
        if path.is_dir():
            _open_windows_folder(path)
            return
        os.startfile(target)  # type: ignore[attr-defined]
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    _open_process(command, title_hint=path.name)


def open_video_with_best_player(path: Path, actual_format: str | None = None) -> None:
    player = select_video_player(path, actual_format)
    if player is None:
        format_label = actual_format or path.suffix.lstrip(".") or "未知"
        raise LocalOpenError(
            "找不到可确认能解码当前视频的播放器。"
            f"当前视频格式：{format_label}。"
            "建议安装 VLC media player 或 mpv 后重试。"
        )
    _open_process([*player.command, str(path)], title_hint=path.name)


def select_video_player(path: Path, actual_format: str | None = None) -> VideoPlayerCandidate | None:
    needs_robust_player = _needs_robust_video_player(path, actual_format)
    candidates = _video_player_candidates()
    for candidate in candidates:
        if candidate.robust:
            return candidate
    if not needs_robust_player:
        return next((candidate for candidate in candidates if not candidate.robust), None)
    return None


def _open_windows_folder(path: Path) -> None:
    process = subprocess.Popen(
        ["explorer.exe", "/n,", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _bring_windows_window_to_front(process.pid, path.name)


def _open_process(command: list[str], title_hint: str | None = None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _bring_windows_window_to_front(process.pid, title_hint)


def _video_player_candidates() -> list[VideoPlayerCandidate]:
    candidates: list[VideoPlayerCandidate] = []
    robust_commands = _robust_video_player_commands()
    candidates.extend(VideoPlayerCandidate(name, command, True) for name, command in robust_commands)
    candidates.extend(VideoPlayerCandidate(name, command, False) for name, command in _common_video_player_commands())
    return candidates


def _robust_video_player_commands() -> list[tuple[str, list[str]]]:
    commands: list[tuple[str, list[str]]] = []
    for name, executable in [
        ("VLC media player", "vlc"),
        ("mpv", "mpv"),
        ("ffplay", "ffplay"),
    ]:
        resolved = shutil.which(executable)
        if resolved:
            commands.append((name, [resolved]))

    if os.name == "nt":
        for name, path in _windows_known_player_paths():
            if path.exists():
                commands.append((name, [str(path)]))
    elif sys.platform == "darwin":
        for app_name in ["IINA", "VLC"]:
            if (Path("/Applications") / f"{app_name}.app").exists():
                commands.append((app_name, ["open", "-a", app_name]))

    return _deduplicate_commands(commands)


def _common_video_player_commands() -> list[tuple[str, list[str]]]:
    if os.name != "nt":
        return []
    commands: list[tuple[str, list[str]]] = []
    for root in _windows_program_roots():
        wmplayer = root / "Windows Media Player" / "wmplayer.exe"
        if wmplayer.exists():
            commands.append(("Windows Media Player", [str(wmplayer)]))
    return _deduplicate_commands(commands)


def _windows_known_player_paths() -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    for root in _windows_program_roots():
        paths.extend(
            [
                ("VLC media player", root / "VideoLAN" / "VLC" / "vlc.exe"),
                ("mpv", root / "mpv" / "mpv.exe"),
                ("PotPlayer", root / "DAUM" / "PotPlayer" / "PotPlayerMini64.exe"),
                ("MPC-HC", root / "MPC-HC" / "mpc-hc64.exe"),
                ("MPC-BE", root / "MPC-BE x64" / "mpc-be64.exe"),
            ]
        )
    return paths


def _windows_program_roots() -> list[Path]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LocalAppData"),
    ]
    return [Path(root) for root in roots if root]


def _deduplicate_commands(commands: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    seen: set[tuple[str, ...]] = set()
    result: list[tuple[str, list[str]]] = []
    for name, command in commands:
        key = tuple(command)
        if key in seen:
            continue
        seen.add(key)
        result.append((name, command))
    return result


def _needs_robust_video_player(path: Path, actual_format: str | None = None) -> bool:
    label = f"{path.suffix} {actual_format or ''}".lower()
    robust_hints = ("webm", "mkv", "vp9", "av01", "av1", "opus", "hevc", "h265")
    return any(hint in label for hint in robust_hints)


def _bring_windows_window_to_front(process_id: int | None, title_hint: str | None = None) -> None:
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    except Exception:
        return

    title_hint_lower = title_hint.lower() if title_hint else None
    for _ in range(20):
        hwnd = _find_window_handle(user32, process_id, title_hint_lower)
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                return
            return
        time.sleep(0.05)


def _find_window_handle(user32: object, process_id: int | None, title_hint_lower: str | None) -> int | None:
    handles: list[int] = []

    def enum_handler(hwnd: int, _lparam: int) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            window_pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            title_value = title.value.lower()
            pid_matches = process_id is not None and window_pid.value == process_id
            title_matches = bool(title_hint_lower and title_hint_lower in title_value)
            if pid_matches or title_matches:
                handles.append(hwnd)
                return False
        except Exception:
            return True
        return True

    callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(enum_handler)
    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return None
    return handles[0] if handles else None

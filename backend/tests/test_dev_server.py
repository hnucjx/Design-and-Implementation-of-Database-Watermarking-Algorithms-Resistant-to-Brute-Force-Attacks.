"""端口预检与「谁占了这个端口」的解析（009）。

解析函数是**纯函数**：用固定的命令输出文本测，不依赖本机此刻的实际状态
（否则换一台机器、或占用者恰好退出，测试就变成随机失败）。
真正绑端口的两个函数用真实 socket 测 —— 它们本来就是「绑一下试试」的语义。
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from collections.abc import Iterator

import pytest

from app.__main__ import _resolve_port
from app.config import AppSettings
from app.dev_server import (
    PortOccupant,
    describe_occupant,
    find_available_port,
    parse_listening_pids,
    parse_tasklist_name,
    port_available,
)

# 一份真实的 `netstat -ano -p tcp` 输出（本机实测片段，含中文表头、IPv6 行、
# 同端口的 ESTABLISHED 行，以及一个必须以 8000 开头的干扰端口 80000）。
NETSTAT_SAMPLE = """活动连接

  协议  本地地址          外部地址        状态           PID
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       6948
  TCP    0.0.0.0:50071          0.0.0.0:0              LISTENING       6948
  TCP    127.0.0.1:50051        0.0.0.0:0              LISTENING       6064
  TCP    [::]:8000              [::]:0                 LISTENING       6948
  TCP    [fe80::2fc7:6e96:99cc:f401%2]:8000  [fe80::2fc7:6e96:99cc:f401%2]:60204  ESTABLISHED     6948
  TCP    0.0.0.0:80000          0.0.0.0:0              LISTENING       1111
"""


@contextmanager
def occupied_port() -> Iterator[int]:
    """占住一个真实端口，直到退出上下文。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        yield listener.getsockname()[1]
    finally:
        listener.close()


@contextmanager
def free_port() -> Iterator[int]:
    """借一个当前可用端口的号，并立刻释放（用于「空闲」断言）。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    yield port


def test_parse_listening_pids_picks_only_the_listening_owner() -> None:
    assert parse_listening_pids(NETSTAT_SAMPLE, 8000) == [6948]


def test_parse_listening_pids_ignores_a_longer_port_with_the_same_prefix() -> None:
    """`:8000` 不能匹配到 `:80000` —— 否则占用者会被报成完全无关的进程。"""
    assert parse_listening_pids(NETSTAT_SAMPLE, 80000) == [1111]


def test_parse_listening_pids_returns_nothing_for_an_unused_port() -> None:
    assert parse_listening_pids(NETSTAT_SAMPLE, 31337) == []


def test_parse_tasklist_name_reads_the_process_name() -> None:
    assert parse_tasklist_name('"Manager.exe","6948","Console","1","182,990,360 K"') == "Manager.exe"


@pytest.mark.parametrize(
    "payload",
    [
        "",  # 空输出
        "\n \n",  # 只有空白
        "信息: 没有运行的任务匹配指定标准。",  # tasklist 查不到 PID 时的本地化提示
        '"System","4","Services","0","8 K"',  # 不是 .exe：宁可不说，也不猜一个错的
    ],
)
def test_parse_tasklist_name_returns_none_when_it_cannot_be_sure(payload: str) -> None:
    assert parse_tasklist_name(payload) is None


def test_port_available_reports_the_truth_both_ways() -> None:
    with occupied_port() as taken:
        assert port_available("127.0.0.1", taken) is False
    with free_port() as idle:
        assert port_available("127.0.0.1", idle) is True


def test_find_available_port_skips_the_occupied_one() -> None:
    with occupied_port() as taken:
        chosen = find_available_port("127.0.0.1", taken, attempts=5)
        assert chosen is not None
        assert chosen > taken
        assert port_available("127.0.0.1", chosen) is True


def test_find_available_port_gives_up_instead_of_scanning_the_whole_range() -> None:
    """attempts 用完就返回 None，不能退化成「扫 65535 个端口」。"""
    with occupied_port() as taken:
        assert find_available_port("127.0.0.1", taken, attempts=1) is None


def test_describe_occupant_finds_nobody_on_a_free_port() -> None:
    with free_port() as idle:
        assert describe_occupant(idle) is None


def test_resolve_port_returns_the_requested_port_when_it_is_free() -> None:
    with free_port() as idle:
        assert _resolve_port("127.0.0.1", idle, auto_port=False) == idle


def test_resolve_port_fails_fast_with_actionable_steps(capsys) -> None:
    with occupied_port() as taken:
        with pytest.raises(SystemExit) as exit_info:
            _resolve_port("127.0.0.1", taken, auto_port=False)

    assert exit_info.value.code == 2
    stderr = capsys.readouterr().err
    assert f"端口 {taken} 无法绑定" in stderr
    assert f"--port {taken + 1}" in stderr
    assert f"YTDL_API_PORT={taken + 1}" in stderr
    assert f"netstat -ano -p tcp | findstr :{taken}" in stderr


def test_resolve_port_moves_on_when_auto_port_is_asked_for(capsys) -> None:
    with occupied_port() as taken:
        chosen = _resolve_port("127.0.0.1", taken, auto_port=True)

    assert chosen > taken
    assert port_available("127.0.0.1", chosen) is True
    assert f"YTDL_API_PORT={chosen}" in capsys.readouterr().err


def test_api_port_default_is_the_documented_one() -> None:
    assert AppSettings.model_fields["api_port"].default == 8000


def test_api_port_can_be_overridden_by_the_shared_env_var(monkeypatch) -> None:
    """前端 Vite 读的是**同一个**变量名，这里把它钉住。"""
    monkeypatch.setenv("YTDL_API_PORT", "8123")
    assert AppSettings().api_port == 8123


def test_env_file_is_anchored_to_the_repository_root() -> None:
    """`.env` 必须是绝对路径：相对路径按 cwd 解析，而用户是 `cd backend` 之后启动的。"""
    from app.config import REPO_ROOT

    assert AppSettings.model_config["env_file"] == REPO_ROOT / ".env"


def test_port_occupant_describes_itself() -> None:
    assert PortOccupant(pid=6948, name="Manager.exe").describe() == "Manager.exe (PID 6948)"
    assert PortOccupant(pid=6948, name=None).describe() == "PID 6948"

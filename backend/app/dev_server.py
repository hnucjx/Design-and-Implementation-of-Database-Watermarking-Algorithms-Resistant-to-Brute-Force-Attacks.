"""启动前的端口检查：把「端口被占」翻译成一句能照着做的话。

为什么值得单独成模块：`python -m uvicorn app.main:app --port 8000` 在端口被别的进程
（本机常见：IncrediBuild 的 `Manager.exe` 以 `0.0.0.0:8000` 通配监听）占着时报的是

    [Errno 13] error while attempting to bind on address ('127.0.0.1', 8000):
    [winerror 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。

这句话有两个坑：一是**根本不是权限问题** —— Windows 在「通配地址已被独占套接字占用」时，
对绑定具体地址返回的是 WSAEACCES(10013) 而不是 WSAEADDRINUSE(10048)；
二是它没告诉你是谁占了、下一步该做什么。

设计约束：
- 解析函数都是**纯函数**（命令输出文本 → 结果），可以离线单测，不依赖本机状态；
- 真正跑命令的部分尽力而为，拿不到就返回 `None` —— 宁可少说一句，也不猜一个错的进程名。
"""

from __future__ import annotations

import csv
import socket
import subprocess
from dataclasses import dataclass
from typing import Final

# netstat / tasklist 都是毫秒级命令，给 5 秒足够；超时宁可当作「查不到」。
_COMMAND_TIMEOUT_SECONDS: Final = 5.0
# netstat 的 State 列（英文，Windows 各语言版本一致）。
_LISTENING_STATE: Final = "LISTENING"
_LOCAL_ADDRESS_COLUMN: Final = 1
_STATE_COLUMN: Final = 3
_PID_COLUMN: Final = 4


@dataclass(frozen=True)
class PortOccupant:
    pid: int
    name: str | None

    def describe(self) -> str:
        return f"{self.name} (PID {self.pid})" if self.name else f"PID {self.pid}"


def port_available(host: str, port: int) -> bool:
    """能不能真的绑上这个地址。

    判据刻意选「绑一下试试」而不是「netstat 里没看到」：被 Hyper-V / WSL / Docker 保留的
    端口段在 netstat 里是干净的，只有真正绑定时才报 10013 / 10048。
    也刻意**不设** `SO_REUSEADDR` —— 在 Windows 上它会让探测在端口已被占用时也「成功」，
    正好把要诊断的情况判反（uvicorn 自己也不设）。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def find_available_port(host: str, preferred: int, *, attempts: int = 20) -> int | None:
    """从 `preferred` 开始往后找第一个能绑的端口；找不到返回 `None`。"""
    upper_bound = min(preferred + attempts, 65536)
    for port in range(preferred, upper_bound):
        if port_available(host, port):
            return port
    return None


def parse_listening_pids(netstat_output: str, port: int) -> list[int]:
    """从 `netstat -ano -p tcp` 的输出里挑出监听该端口的 PID（去重，按出现顺序）。

    只认 `LISTENING` 行：同一端口还有大量 `ESTABLISHED` 行（远端端口恰好也是 8000 时），
    那些是连接而不是占用者。
    """
    suffix = f":{port}"
    pids: list[int] = []
    for line in netstat_output.splitlines():
        columns = line.split()
        if len(columns) <= _PID_COLUMN:
            continue
        if columns[_STATE_COLUMN].upper() != _LISTENING_STATE:
            continue
        if not columns[_LOCAL_ADDRESS_COLUMN].endswith(suffix):
            continue
        try:
            pid = int(columns[_PID_COLUMN])
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def parse_tasklist_name(tasklist_csv: str) -> str | None:
    """从 `tasklist /FO CSV /NH` 的输出里取进程名；取不到返回 `None`。

    要求第一格看起来像可执行文件（以 `.exe` 结尾）：`tasklist` 查不到 PID 时输出的是
    本地化提示（「信息: 没有运行的任务匹配指定标准。」），那样的一行不能被当成进程名。
    """
    rows = [line for line in tasklist_csv.splitlines() if line.strip()]
    if not rows:
        return None
    row = next(csv.reader([rows[0]]), [])
    if not row or not row[0].lower().endswith(".exe"):
        return None
    return row[0]


def describe_occupant(port: int) -> PortOccupant | None:
    """尽力找出谁在监听这个端口；非 Windows、命令不可用、解析不出来时都返回 `None`。"""
    for pid in parse_listening_pids(_run(["netstat", "-ano", "-p", "tcp"]), port):
        name = parse_tasklist_name(_run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]))
        return PortOccupant(pid=pid, name=name)
    return None


def _run(command: list[str]) -> str:
    """跑一条只读命令并拿到 stdout；任何失败都退化成空字符串。"""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""

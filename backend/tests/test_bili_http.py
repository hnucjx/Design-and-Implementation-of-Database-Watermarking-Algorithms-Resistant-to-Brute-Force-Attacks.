"""`bili_http.UrllibTransport` 的真实网络行为（对着本机 HTTP 服务器跑）。

这是 B站 链路的**唯二接缝**之一，而它的未覆盖部分正好是整个发送路径（59%）。
这些行为只能靠真发一次请求来钉：参数合并、表单/JSON 两种 body、请求头覆盖、
非 2xx 也要把正文带回来（B站 有时把业务错误放在 4xx 里）、连不上与超时统一成 `HttpError`。

不起外网：一个 `ThreadingHTTPServer` 就在本进程里，断言的是「我们发出去的字节」。
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
import urllib.parse
from types import SimpleNamespace

import pytest

from app.bilibili.bili_http import DEFAULT_TIMEOUT, HttpError, HttpResult, UrllibTransport


class _State(SimpleNamespace):
    status: int = 200
    body: bytes = b'{"code":0}'
    delay: float = 0.0
    requests: list[dict]


@pytest.fixture
def server():
    state = _State(requests=[])

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # 静音：用例失败时不要被访问日志淹掉
            pass

        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            state.requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": {key.lower(): value for key, value in self.headers.items()},
                    "body": body,
                }
            )
            if state.delay:
                time.sleep(state.delay)
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "SESSDATA=abc; Path=/")
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        do_GET = _handle
        do_POST = _handle

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield UrllibTransport(), f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


def closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_query_parameters_are_appended_and_none_values_dropped(server) -> None:
    transport, base, state = server

    transport.request("GET", f"{base}/x", params={"a": 1, "b": None, "c": "中文"})

    query = urllib.parse.parse_qs(urllib.parse.urlsplit(state.requests[0]["path"]).query)
    assert query == {"a": ["1"], "c": ["中文"]}


def test_an_existing_query_string_is_extended_not_replaced(server) -> None:
    transport, base, state = server

    transport.request("GET", f"{base}/x?already=1", params={"next": 2})

    assert state.requests[0]["path"] == "/x?already=1&next=2"


def test_a_form_body_is_urlencoded(server) -> None:
    transport, base, state = server

    transport.request("POST", f"{base}/x", form={"csrf": "tok", "title": "标题"})

    sent = state.requests[0]
    assert sent["headers"]["content-type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(sent["body"].decode("utf-8")) == {"csrf": ["tok"], "title": ["标题"]}


def test_a_json_body_keeps_non_ascii_characters(tmp_path, server) -> None:
    transport, base, state = server

    transport.request("POST", f"{base}/x", json_body={"title": "中文标题"})

    sent = state.requests[0]
    assert sent["headers"]["content-type"] == "application/json"
    # `ensure_ascii=False`：转义成 \uXXXX 也能用，但日志里就看不出用户填了什么。
    assert "中文标题" in sent["body"].decode("utf-8")


def test_custom_headers_override_the_defaults(server) -> None:
    transport, base, state = server

    transport.request("GET", f"{base}/x", headers={"Referer": "https://member.bilibili.com/x"})

    sent = state.requests[0]
    assert sent["headers"]["referer"] == "https://member.bilibili.com/x"
    # 没覆盖的仍然在（UA 是 B站 会员接口的硬要求之一）。
    assert "Mozilla" in sent["headers"]["user-agent"]


def test_a_non_2xx_response_returns_its_body_instead_of_raising(server) -> None:
    transport, base, state = server
    state.status = 403
    state.body = b'{"code":-101,"message":"not logged in"}'

    result = transport.request("GET", f"{base}/x")

    assert isinstance(result, HttpResult)
    assert result.status_code == 403
    # 正文必须回来：B站 有时把业务错误放在非 200 的响应里，丢掉它就没法排障。
    assert result.json()["code"] == -101


def test_set_cookie_headers_are_collected(server) -> None:
    """扫码登录靠它：凭据可能只出现在 `Set-Cookie` 里。"""
    transport, base, _ = server

    result = transport.request("GET", f"{base}/x")

    assert result.set_cookie == ("SESSDATA=abc; Path=/",)


def test_a_connection_error_becomes_a_http_error() -> None:
    transport = UrllibTransport()

    with pytest.raises(HttpError) as excinfo:
        transport.request("GET", f"http://127.0.0.1:{closed_port()}/x", timeout=2.0)

    assert "请求 B站 失败" in str(excinfo.value)


def test_a_timeout_becomes_a_http_error_instead_of_hanging(server) -> None:
    transport, base, state = server
    state.delay = 0.5

    started = time.monotonic()
    with pytest.raises(HttpError):
        transport.request("GET", f"{base}/x", timeout=0.1)

    # 判据是「真的按超时回来了」，不是「最终会失败」。
    assert time.monotonic() - started < 2.0


def test_a_non_json_body_is_reported_as_a_http_error() -> None:
    result = HttpResult(status_code=200, text="<html>502 Bad Gateway</html>")

    with pytest.raises(HttpError) as excinfo:
        result.json()

    assert "没有返回 JSON" in str(excinfo.value)
    assert "502 Bad Gateway" in str(excinfo.value)


def test_the_default_timeout_is_long_enough_for_a_slow_platform() -> None:
    # 20 s：比任何一次正常往返都长，又不至于让界面等成「没有终点」。
    assert DEFAULT_TIMEOUT == 20.0


# ================================================================ 地址族与总预算
def test_all_addresses_share_one_deadline(monkeypatch) -> None:
    """`timeout` 是**这次请求**的预算，不是每个地址各给一份。

    缺陷原样（ai/bug-fix/031）：`http.client` 会把解析出来的地址逐个试一遍，每次都拿满
    `timeout`。本机到 B站 有 6 个 IPv6 地址全是黑洞 ⇒ 写 `timeout=20` 实测走了 **160 秒**，
    于是「重新体检」永远超时、扫码永远转圈。

    用**假 socket** 而不是真地址：`127.0.0.1` 的关闭端口是**立即拒连**，给几份预算都是毫秒级，
    测不出差别；只有「连上去没回应」的黑洞才能把预算问题暴露出来。
    """
    from app.bilibili import bili_http

    slept: list[float] = []

    class _BlackHole:
        """连上去就等到自己那份 timeout 用完 —— 与 IPv6 黑洞的行为一致。"""

        def __init__(self, *args, **kwargs) -> None:
            self._timeout: float | None = None

        def settimeout(self, value: float) -> None:
            self._timeout = value

        def connect(self, address) -> None:
            waited = max(0.0, float(self._timeout or 0.0))
            slept.append(waited)
            time.sleep(waited)
            raise OSError("timed out")

        def close(self) -> None:
            return None

    monkeypatch.setattr(bili_http.socket, "socket", _BlackHole)
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 443)),
    ]

    started = time.monotonic()
    with pytest.raises(OSError):
        bili_http._connect_within(addresses, timeout=0.4)
    elapsed = time.monotonic() - started

    # 判据一：总耗时守住 0.4 秒（每个地址各给一份的话是 0.8 秒）。
    assert elapsed < 0.7
    # 判据二：预算用完就**不再试下一个地址** —— 只尝试过 1 次。
    # 这条是刻意的取舍：宁可报「超时」，也不让一次请求拖成 N 倍；
    # 只试一个地址的代价是「第一个地址不通但第二个通」时这次会失败，下一次重试仍会用到第二个。
    assert len(slept) == 1
    assert slept[0] <= 0.45


def test_the_transport_only_resolves_ipv4(monkeypatch) -> None:
    """只解析 IPv4。

    判据是**解析时传的 `family`**，不是「连没连上」：本机 IPv6 是黑洞（`getaddrinfo` 正常返回、
    连上去没回应），所以「连上了」这件事在 IPv6 被黑洞时也能通过（只是慢 160 秒）。
    只有盯住 `family` 才拦得住下一次有人把这段改回默认顺序。
    """
    import socket as socket_module

    from app.bilibili.bili_http import UrllibTransport

    real = socket_module.getaddrinfo
    families: list[int] = []

    def spy(host, port, family=0, *args, **kwargs):
        families.append(family)
        return real(host, port, family, *args, **kwargs)

    monkeypatch.setattr(socket_module, "getaddrinfo", spy)
    with pytest.raises(HttpError):
        # 连一个必然失败的本机端口：只要它走到了解析这一步就够了。
        UrllibTransport().request("GET", f"http://127.0.0.1:{closed_port()}/x", timeout=1.0)

    assert families == [socket_module.AF_INET]

"""B站 请求的 HTTP 接缝。

单独成模块的理由是**它要被两个模块共用**（`bili_auth` 的扫码登录、`bili_api` 的会员态接口），
而且它是**唯一需要被替换掉的东西** —— 自动测试注入一个假 transport 就能完全离线跑完
投稿、对账与归集的全部分支（见 [02-design.md §8](../ai/feature/bilibili-upload/02-design.md)）。

两个刻意的决定：

1. **不用传递依赖里的 `requests`。** `requests` 在本机是可导入的，但它不在 `pyproject.toml`
   的 `dependencies` 里（是 yt-dlp 带进来的）—— 靠传递依赖写代码，某天上游改依赖就会断。
2. **B站 请求强制直连，不走用户配置的代理。** 本仓库的代理是给 YouTube 用的；拿它去请求 B站
   轻则绕远、重则被拦成 502 且报错与真实病因无关（arXiv 项目踩过这条，代码里直接写死了 `NO_PROXY`）。
"""

from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_TIMEOUT = 20.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
WEB_REFERER = "https://www.bilibili.com/"
MEMBER_REFERER = "https://member.bilibili.com/platform/upload-manager/article"


class HttpError(RuntimeError):
    """网络层失败（连不上、超时、非 2xx）。**业务错误码不走这里** —— 那些是 HTTP 200 + `code != 0`。"""


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    text: str
    # 扫码登录需要它：凭据可能只出现在 Set-Cookie 里，而不在响应正文的回调 URL 上。
    set_cookie: tuple[str, ...] = ()

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError as exc:
            raise HttpError(f"接口没有返回 JSON（HTTP {self.status_code}）：{self.text[:200]}") from exc


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResult: ...


def _connect_within(addresses: list[tuple], timeout: float) -> socket.socket:
    """按顺序逐个连，**整段共享一个 timeout**。

    为什么不能每个地址各给一份 `timeout`：`timeout` 是**单次 socket 操作**的预算，不是这次请求的
    总时长。`create_connection` 会把解析出来的每个地址都试一遍，于是「20 秒超时」在 6 个地址上
    等于 120 秒 —— 实测一次 `api.bilibili.com` 要走 **160 秒**才报错（见 ai/bug-fix/027）。
    调用方写 `timeout=20` 时想要的是「最多等 20 秒」，所以这里按**截止时刻**分配。
    """
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    for family, socktype, proto, _canonical, address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise last_error or TimeoutError(f"连不上对端：{timeout:g} 秒用完了")


class _IPv4Mixin:
    """只解析 IPv4。

    本机到 B站 的 IPv6 是**黑洞**（`getaddrinfo` 正常返回、连上去没有任何回应），而默认顺序把
    IPv6 排在前面。实测同一个 URL：默认顺序 **160 s**、强制 IPv4 **0.44 s**。
    这不会报错，只会让「重新体检」永远超时、扫码永远转圈 —— 用户看到的是两个不相干的错误消息。
    """

    host: str
    port: int
    timeout: float

    def _open_ipv4(self) -> socket.socket:
        infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        return _connect_within(list(infos), float(self.timeout or DEFAULT_TIMEOUT))


class _IPv4HTTPConnection(_IPv4Mixin, http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = self._open_ipv4()


class _IPv4HTTPSConnection(_IPv4Mixin, http.client.HTTPSConnection):
    def connect(self) -> None:
        sock = self._open_ipv4()
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            sock = self.sock
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _IPv4HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):  # noqa: ANN001, ANN201 - 与标准库签名一致
        return self.do_open(_IPv4HTTPConnection, req)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):  # noqa: ANN001, ANN201 - 与标准库签名一致
        return self.do_open(_IPv4HTTPSConnection, req, context=self._context)


class UrllibTransport:
    """标准库实现。**显式清空 ProxyHandler**，见模块文档第 2 条。

    连接层用自己那对 `HTTP(S)Connection`，理由见模块文档第 3、4 条（IPv4 与总预算）。
    """

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _IPv4HTTPHandler(),
            _IPv4HTTPSHandler(),
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        form: dict[str, Any] | None = None,
        json_body: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> HttpResult:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        body: bytes | None = None
        final_headers = {"User-Agent": USER_AGENT, "Referer": WEB_REFERER}
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            final_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            final_headers["Content-Type"] = "application/json"
        final_headers.update(headers or {})

        request = urllib.request.Request(url, data=body, headers=final_headers, method=method.upper())
        try:
            with self._opener.open(request, timeout=timeout) as response:
                return HttpResult(
                    status_code=response.status,
                    text=response.read().decode("utf-8", "replace"),
                    set_cookie=tuple(response.headers.get_all("Set-Cookie") or ()),
                )
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 也把正文带回来：B站 有时把业务错误放在非 200 的响应里，丢掉正文就没法排障。
            return HttpResult(
                status_code=exc.code,
                text=exc.read().decode("utf-8", "replace"),
                set_cookie=tuple(exc.headers.get_all("Set-Cookie") or ()) if exc.headers else (),
            )
        except Exception as exc:  # noqa: BLE001 - 统一成一种网络失败
            raise HttpError(f"请求 B站 失败：{exc}") from exc


def default_transport() -> Transport:
    return UrllibTransport()

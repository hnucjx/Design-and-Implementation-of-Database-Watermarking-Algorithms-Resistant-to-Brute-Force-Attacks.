import urllib.error
import urllib.request

from app import connectivity
from app.connectivity import ProxyTestResult, build_opener
from app.connectivity import test_proxy as probe_proxy
from app.proxy import PROXY_SOURCE_DIRECT, PROXY_SOURCE_SETTING, resolve_proxy


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class FakeOpener:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.requests: list[object] = []

    def open(self, request, timeout=None):  # noqa: ANN001, ANN201
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _patch_opener(monkeypatch, outcome: object) -> FakeOpener:
    opener = FakeOpener(outcome)
    monkeypatch.setattr(connectivity, "build_opener", lambda resolution: opener)
    return opener


def _proxy_handler(opener: urllib.request.OpenerDirector) -> urllib.request.ProxyHandler:
    handlers = [handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)]
    assert handlers, "opener 必须显式带一个 ProxyHandler"
    return handlers[0]


def _clear_proxy_environment(monkeypatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_build_opener_uses_the_resolved_proxy() -> None:
    opener = build_opener(resolve_proxy("127.0.0.1:7890"))

    assert _proxy_handler(opener).proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_build_opener_disables_proxies_entirely_for_direct() -> None:
    """``ProxyHandler({})`` 才是「不用代理」；不传 handler 会让 urllib 去读环境变量。

    注意断言的是**语义**而不是实现：CPython 会把空映射的 ProxyHandler 丢掉
    （它不注册 *_open 方法），两种结果都等价于「不代理」。
    """
    opener = build_opener(resolve_proxy("direct"))

    proxy_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert all(handler.proxies == {} for handler in proxy_handlers)


def test_ok_probe_reports_status_bytes_and_proxy(monkeypatch) -> None:
    _patch_opener(monkeypatch, FakeResponse(200, b"User-agent: *\n"))

    result = probe_proxy(resolve_proxy("127.0.0.1:7890"))

    assert isinstance(result, ProxyTestResult)
    assert result.ok is True
    assert result.http_status == 200
    assert result.bytes_read == len(b"User-agent: *\n")
    assert result.source == PROXY_SOURCE_SETTING
    assert result.proxy == "http://127.0.0.1:7890"
    assert "HTTP 200" in result.summary
    assert result.next_steps == []


def test_proxy_credentials_are_redacted_in_the_result(monkeypatch) -> None:
    _patch_opener(monkeypatch, FakeResponse(200, b"ok"))

    result = probe_proxy(resolve_proxy("http://alice:s3cret@127.0.0.1:7890"))

    assert "s3cret" not in (result.proxy or "")
    assert "alice:***@" in (result.proxy or "")


def test_connection_refused_is_reported_as_a_failure_with_next_steps(monkeypatch) -> None:
    _patch_opener(monkeypatch, urllib.error.URLError("Connection refused"))

    result = probe_proxy(resolve_proxy("127.0.0.1:7890"))

    assert result.ok is False
    assert "Connection refused" in (result.error or "")
    assert result.elapsed_ms >= 0
    assert result.next_steps


def test_http_error_is_not_treated_as_success(monkeypatch) -> None:
    error = urllib.error.HTTPError("https://x", 407, "Proxy Authentication Required", {}, None)  # type: ignore[arg-type]
    _patch_opener(monkeypatch, error)

    result = probe_proxy(resolve_proxy("127.0.0.1:7890"))

    assert result.ok is False
    assert result.http_status == 407
    assert "407" in (result.error or "")


def test_a_200_with_an_empty_body_is_not_a_success(monkeypatch) -> None:
    _patch_opener(monkeypatch, FakeResponse(200, b""))

    result = probe_proxy(resolve_proxy("127.0.0.1:7890"))

    assert result.ok is False


def test_direct_failure_message_says_it_may_be_expected(monkeypatch) -> None:
    """强制直连失败时不该吓唬用户：很多网络下这是预期结果。"""
    _patch_opener(monkeypatch, urllib.error.URLError("timed out"))

    result = probe_proxy(resolve_proxy("direct"))

    assert result.source == PROXY_SOURCE_DIRECT
    assert result.proxy is None
    assert "预期" in result.summary


def test_no_proxy_source_says_no_proxy_was_found(monkeypatch) -> None:
    _patch_opener(monkeypatch, urllib.error.URLError("timed out"))
    _clear_proxy_environment(monkeypatch)

    result = probe_proxy(resolve_proxy(""))

    if result.source == "none":  # 本机注册表可能仍有系统代理，只在确实为空时断言
        assert "没有发现任何代理" in result.summary

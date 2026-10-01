import http.cookiejar
import time
from pathlib import Path
import urllib.request

from app import cookie_health
from app.cookie_health import inspect_cookies_file, verify_cookies
from app.proxy import resolve_proxy


HEADER = "# Netscape HTTP Cookie File\n"


def write_jar(tmp_path: Path, rows: list[tuple[str, str, int]]) -> Path:
    """rows = (domain, name, expires_at)。"""
    path = tmp_path / "cookies.txt"
    lines = [HEADER]
    for domain, name, expiry in rows:
        lines.append(f"{domain}\tTRUE\t/\tTRUE\t{expiry}\t{name}\tvalue-{name}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_missing_file_says_how_to_get_one(tmp_path: Path) -> None:
    health = inspect_cookies_file(tmp_path / "cookies.txt")

    assert health.present is False
    assert health.cookie_count == 0
    assert health.verdict == "没有 cookies 文件"
    assert any("export_cookies_via_cdp" in step for step in health.next_steps)


def test_google_com_only_jar_is_called_out_as_useless(tmp_path: Path) -> None:
    """这是真实踩过的坑：文件里有 SID，但域名是 .google.com，对 youtube.com 无效。"""
    path = write_jar(tmp_path, [(".google.com", "SID", 0), (".google.com", "HSID", 0)])

    health = inspect_cookies_file(path)

    assert health.cookie_count == 2
    assert health.youtube_domain_count == 0
    assert "非 youtube.com 域" in health.verdict
    assert any(".google.com" in step for step in health.next_steps)


def test_anonymous_only_jar_is_distinguished_from_a_useless_one(tmp_path: Path) -> None:
    path = write_jar(tmp_path, [(".youtube.com", "VISITOR_INFO1_LIVE", 0), (".youtube.com", "YSC", 0)])

    health = inspect_cookies_file(path)

    assert health.youtube_domain_count == 2
    assert health.anonymous_only is True
    assert health.auth_cookie_names == []
    assert "只有匿名 cookie" in health.verdict


def test_healthy_jar_reports_the_auth_cookies_it_found(tmp_path: Path) -> None:
    future = int(time.time()) + 86400
    path = write_jar(
        tmp_path,
        [(".youtube.com", "SID", future), (".youtube.com", "HSID", future), (".youtube.com", "SAPISID", future)],
    )

    health = inspect_cookies_file(path)

    assert health.format_ok is True
    assert health.youtube_domain_count == 3
    assert health.auth_cookie_names == ["HSID", "SAPISID", "SID"]
    assert "SID" not in health.missing_auth_cookie_names
    assert "包含 youtube.com 域下的鉴权 cookie" in health.verdict


def test_expired_cookies_are_counted(tmp_path: Path) -> None:
    past = int(time.time()) - 86400
    path = write_jar(tmp_path, [(".youtube.com", "SID", past)])

    health = inspect_cookies_file(path)

    assert health.expired_count == 1
    assert "过期" in health.verdict


def test_json_paste_is_rejected_with_a_format_message(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text('{"cookies": [{"name": "SID"}]}', encoding="utf-8")

    health = inspect_cookies_file(path)

    assert health.cookie_count == 0
    assert health.format_ok is False
    assert "格式" in health.verdict


def test_malformed_rows_are_reported_but_valid_rows_still_count(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(f"{HEADER}.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tvalue\ngarbage-line\n", encoding="utf-8")

    health = inspect_cookies_file(path)

    assert health.cookie_count == 1
    assert health.format_ok is False
    assert "7 列" in health.format_note


def test_deep_verification_marks_a_valid_jar_as_logged_in(tmp_path: Path, monkeypatch) -> None:
    future = int(time.time()) + 86400
    path = write_jar(tmp_path, [(".youtube.com", "SID", future)])
    monkeypatch.setattr(cookie_health, "probe_logged_in", lambda *_args, **_kwargs: (True, "page says true"))

    health = verify_cookies(path, resolve_proxy("direct"), deep=True)

    assert health.logged_in is True
    assert health.verdict == "登录态有效：YouTube 已识别为已登录"


def test_deep_verification_marks_an_expired_session_as_invalid(tmp_path: Path, monkeypatch) -> None:
    future = int(time.time()) + 86400
    path = write_jar(tmp_path, [(".youtube.com", "SID", future)])
    monkeypatch.setattr(cookie_health, "probe_logged_in", lambda *_args, **_kwargs: (False, "page says false"))

    health = verify_cookies(path, resolve_proxy("direct"), deep=True)

    assert health.logged_in is False
    assert "登录态无效" in health.verdict
    assert any("重新导出" in step for step in health.next_steps)


def test_offline_mode_does_not_touch_the_network(tmp_path: Path, monkeypatch) -> None:
    path = write_jar(tmp_path, [(".youtube.com", "SID", int(time.time()) + 86400)])

    def forbidden(*_args, **_kwargs):
        raise AssertionError("deep=False 时不该联网")

    monkeypatch.setattr(cookie_health, "probe_logged_in", forbidden)

    health = verify_cookies(path, resolve_proxy("direct"), deep=False)

    assert health.logged_in is None
    assert "包含 youtube.com 域下的鉴权 cookie" in health.verdict


def test_probe_logged_in_reads_the_jar_and_reports_true(tmp_path: Path, monkeypatch) -> None:
    future = int(time.time()) + 86400
    path = write_jar(tmp_path, [(".youtube.com", "SID", future)])

    class Response:
        def read(self, _size: int = -1) -> bytes:
            return b'{"LOGGED_IN":true}'

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    class Opener:
        def add_handler(self, handler) -> None:  # noqa: ANN001
            assert isinstance(handler, urllib.request.HTTPCookieProcessor)

        def open(self, request, timeout=None):  # noqa: ANN001, ANN201
            return Response()

    monkeypatch.setattr(cookie_health, "build_opener", lambda resolution: Opener())

    logged_in, detail = cookie_health.probe_logged_in(path, resolve_proxy("direct"))

    assert logged_in is True
    assert "LOGGED_IN" in detail


def test_probe_logged_in_reports_false_clearly(tmp_path: Path, monkeypatch) -> None:
    path = write_jar(tmp_path, [(".youtube.com", "SID", int(time.time()) + 86400)])

    class Response:
        def read(self, _size: int = -1) -> bytes:
            return b'{"LOGGED_IN":false}'

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    class Opener:
        def add_handler(self, handler) -> None:  # noqa: ANN001
            pass

        def open(self, request, timeout=None):  # noqa: ANN001, ANN201
            return Response()

    monkeypatch.setattr(cookie_health, "build_opener", lambda resolution: Opener())

    logged_in, detail = cookie_health.probe_logged_in(path, resolve_proxy("direct"))

    assert logged_in is False
    assert "匿名" in detail

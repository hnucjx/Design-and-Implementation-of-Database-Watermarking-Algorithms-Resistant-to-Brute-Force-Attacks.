"""代理解析（app/proxy.py）的单元测试。

这些用例刻意**不触碰真实注册表/环境**：一律 monkeypatch 掉两个「来源」读取函数，
否则结果会随机器状态变化（本机就有系统代理），测试就失去意义了。
"""

import pytest

from app import proxy as proxy_module
from app.proxy import (
    PROXY_SOURCE_DIRECT,
    PROXY_SOURCE_ENVIRONMENT,
    PROXY_SOURCE_NONE,
    PROXY_SOURCE_SETTING,
    PROXY_SOURCE_SYSTEM,
    _proxy_from_server_value,
    has_no_proxy_bypass,
    normalize_proxy_url,
    redact_proxy_credentials,
    resolve_proxy,
)


def stub_sources(monkeypatch, system: str | None = None, environment: str | None = None) -> None:
    monkeypatch.setattr(proxy_module, "read_system_proxy", lambda: system)
    monkeypatch.setattr(proxy_module, "read_environment_proxy", lambda: environment)


@pytest.mark.parametrize("setting", ["direct", "DIRECT", "direct://", "none", "off", "no", "-"])
def test_direct_sentinels_force_direct(monkeypatch, setting: str) -> None:
    stub_sources(monkeypatch, system="http://127.0.0.1:7890", environment="http://127.0.0.1:54109")

    resolution = resolve_proxy(setting)

    assert resolution.source == PROXY_SOURCE_DIRECT
    # 空串是 yt-dlp 的「强制直连」写法（YoutubeDL.proxies 会映射成 __noproxy__），
    # 不能被当成「没值」而丢掉。
    assert resolution.url == ""
    assert resolution.to_ydl_options() == {"proxy": ""}


@pytest.mark.parametrize("setting", [None, "", "   ", "auto", "system"])
def test_blank_or_auto_setting_means_auto(monkeypatch, setting: str | None) -> None:
    stub_sources(monkeypatch)

    resolution = resolve_proxy(setting)

    assert resolution.source == PROXY_SOURCE_NONE
    assert resolution.url is None
    assert resolution.to_ydl_options() == {}


def test_explicit_setting_wins_and_gets_a_scheme(monkeypatch) -> None:
    stub_sources(monkeypatch, system="http://127.0.0.1:7890", environment="http://127.0.0.1:54109")

    resolution = resolve_proxy("127.0.0.1:10809")

    assert resolution.source == PROXY_SOURCE_SETTING
    assert resolution.url == "http://127.0.0.1:10809"
    assert resolution.to_ydl_options() == {"proxy": "http://127.0.0.1:10809"}


def test_system_proxy_outranks_environment(monkeypatch) -> None:
    """桌面应用应和浏览器一致：环境变量不该静默顶掉 Windows 系统代理。"""

    stub_sources(monkeypatch, system="http://127.0.0.1:7890", environment="http://127.0.0.1:54109")

    resolution = resolve_proxy(None)

    assert resolution.source == PROXY_SOURCE_SYSTEM
    assert resolution.url == "http://127.0.0.1:7890"
    assert resolution.to_ydl_options() == {"proxy": "http://127.0.0.1:7890"}


def test_environment_proxy_is_reported_but_not_written(monkeypatch) -> None:
    """环境变量来源不覆盖 yt-dlp 自身的解析，以保留 NO_PROXY 的绕过语义。"""

    stub_sources(monkeypatch, system=None, environment="http://127.0.0.1:54109")

    resolution = resolve_proxy(None)

    assert resolution.source == PROXY_SOURCE_ENVIRONMENT
    assert resolution.url == "http://127.0.0.1:54109"
    assert resolution.writes_ydl_option is False
    assert resolution.to_ydl_options() == {}


def test_resolution_reports_both_sources_for_diagnostics(monkeypatch) -> None:
    stub_sources(monkeypatch, system="http://127.0.0.1:7890", environment="http://127.0.0.1:54109")

    resolution = resolve_proxy(None)

    assert resolution.system_proxy == "http://127.0.0.1:7890"
    assert resolution.environment_proxy == "http://127.0.0.1:54109"


def test_no_proxy_bypass_is_detected(monkeypatch) -> None:
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    assert has_no_proxy_bypass() is False

    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    assert has_no_proxy_bypass() is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("  http://127.0.0.1:7890  ", "http://127.0.0.1:7890"),
        ("", ""),
    ],
)
def test_normalize_proxy_url(raw: str, expected: str) -> None:
    assert normalize_proxy_url(raw) == expected


def test_registry_single_server_value() -> None:
    assert _proxy_from_server_value("127.0.0.1:7890") == "http://127.0.0.1:7890"


def test_registry_multi_scheme_prefers_https() -> None:
    assert _proxy_from_server_value("http=127.0.0.1:7890;https=127.0.0.1:7891") == "http://127.0.0.1:7891"


def test_registry_socks_only_keeps_socks_scheme() -> None:
    # scheme 必须留在 URL 上：否则会被当成 http 代理，握手直接失败。
    assert _proxy_from_server_value("socks=127.0.0.1:1080") == "socks://127.0.0.1:1080"


@pytest.mark.parametrize("raw", ["", "   ", "=", "http=;"])
def test_registry_garbage_yields_nothing(raw: str) -> None:
    assert _proxy_from_server_value(raw) is None


def test_redact_proxy_credentials() -> None:
    assert redact_proxy_credentials("http://user:secret@127.0.0.1:7890") == "http://user:***@127.0.0.1:7890"
    assert redact_proxy_credentials("http://127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert redact_proxy_credentials(None) is None

from app.error_advice import advise, exception_chain


class DownloadError(RuntimeError):
    pass


def test_page_needs_to_be_reloaded_is_explained_as_a_js_runtime_problem() -> None:
    """这是本项目最贵的一句天书，必须被翻译成「JS 运行时没跑起来」+ 下一步。"""
    exc = DownloadError("ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.")

    advice = advise(exc, js_runtime_error="启动失败：ERR_ACCESS_DENIED")

    assert advice is not None
    assert advice.code == "js_runtime_challenge_failed"
    assert "ERR_ACCESS_DENIED" in advice.detail
    assert advice.next_steps
    # 日志块要能让人直接照做。
    assert "建议1" in advice.to_log_block()


def test_n_challenge_warning_is_matched_case_insensitively() -> None:
    exc = DownloadError("WARNING: n challenge solving failed: Some formats may be missing.")

    advice = advise(exc, js_runtime_available=False)

    assert advice is not None
    assert advice.code == "js_runtime_challenge_failed"
    assert any("Deno" in step or "Node" in step for step in advice.next_steps)


def test_js_challenge_takes_priority_over_the_generic_format_error() -> None:
    """判定顺序：更具体的病因必须先命中，不能被别的词吃掉。"""
    exc = DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden\nn challenge solving failed")

    advice = advise(exc)

    assert advice is not None
    assert advice.code == "js_runtime_challenge_failed"


def test_cookie_required_error_reports_current_cookie_state() -> None:
    exc = DownloadError("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies")

    advice = advise(exc, cookies_configured=False)

    assert advice is not None
    assert advice.code == "cookie_required"
    assert "未配置" in advice.detail
    assert any("from-browser" in step or "export_cookies_via_cdp" in step for step in advice.next_steps)


def test_proxy_failure_is_reported_with_the_effective_proxy() -> None:
    exc = DownloadError("urllib.error.URLError: <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>")

    advice = advise(exc, proxy_source="setting", proxy_url="http://127.0.0.1:7890")

    assert advice is not None
    assert advice.code == "proxy_unreachable"
    assert "127.0.0.1:7890" in advice.detail
    assert "setting" in advice.detail


def test_media_stream_blocked_still_maps_to_the_existing_category() -> None:
    exc = DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    advice = advise(exc)

    assert advice is not None
    assert advice.code == "media_stream_blocked"


def test_unclassifiable_error_returns_none_instead_of_guessing() -> None:
    assert advise(DownloadError("Requested format is not available")) is None
    assert advise(ValueError()) is None


def test_exception_chain_walks_cause_and_context_without_looping() -> None:
    root = ValueError("root")
    middle = RuntimeError("middle")
    middle.__cause__ = root
    outer = DownloadError("outer")
    outer.__context__ = middle

    messages = [str(item) for item in exception_chain(outer)]

    assert messages == ["outer", "middle", "root"]


def test_advice_matches_on_a_wrapped_cause() -> None:
    """yt-dlp 的异常会被包好几层，只看最外层是不够的。"""
    inner = DownloadError("ERROR: [youtube] x: The page needs to be reloaded.")
    outer = RuntimeError("yt-dlp failed")
    outer.__cause__ = inner

    advice = advise(outer)

    assert advice is not None
    assert advice.code == "js_runtime_challenge_failed"

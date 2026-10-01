import importlib.metadata
from pathlib import Path
import sys
from types import SimpleNamespace
from http.cookiejar import Cookie

import pytest
import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import DownloadError

from app.config import AppSettings
from app.schemas import DownloadOptions, FormatOption
from app import ytdlp_service
from app.ytdlp_service import BrowserCookieImportError, YtDlpService


def reset_js_runtime_probe(monkeypatch, service: YtDlpService, failure: str | None = None) -> None:
    """让 ``_detect_js_runtime`` 可以重新探测，且不必真的去起子进程。

    两个都是必需的：``YtDlpService.__init__`` 会立刻探测一次并记忆化，测试改完候选必须
    先清缓存；而测试里用的假可执行文件（``write_bytes(b"")``）本来就跑不起来，
    所以自检要一起打桩。
    """
    service.reset_js_runtime_cache()
    monkeypatch.setattr(service, "_probe_js_runtime", lambda name, executable: failure)


def test_resolution_option_limits_best_video_height(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))
    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="1080p"),
        cookies_path=None,
    )

    assert opts["format"] == (
        "bv*[height=1080][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
        "bv*[height=1080][ext=mp4]+ba[ext=m4a]/"
        "bv*[height=1080]+ba/"
        "b[height=1080][protocol^=m3u8]/"
        "b[height=1080]"
    )
    assert "/best" not in opts["format"]
    assert "height<=1080" not in opts["format"]
    assert opts["merge_output_format"] == "mp4"
    assert opts["ffmpeg_location"] == str(tmp_path / "ffmpeg.exe")
    assert opts["skip_download"] is False


def test_http_403_error_detection_handles_forbidden_video_data(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.is_http_403_error(
        RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    )
    assert service.is_http_403_error(RuntimeError("HTTP Error 403: Forbidden"))
    assert not service.is_http_403_error(RuntimeError("HTTP Error 404: Not Found"))


def test_http_403_error_detection_checks_exception_context(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    try:
        try:
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        except RuntimeError as exc:
            raise AssertionError() from exc
    except AssertionError as exc:
        assert str(exc) == ""
        assert service.is_http_403_error(exc)


def test_readable_error_message_uses_context_when_top_level_message_is_empty(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    try:
        try:
            raise RuntimeError("inner failure")
        except RuntimeError as exc:
            raise AssertionError() from exc
    except AssertionError as exc:
        assert service.readable_error_message(exc) == "inner failure"


def test_media_stream_blocked_detection_handles_connection_reset(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.is_media_stream_blocked_error(
        RuntimeError(
            "ERROR: [download] Got error: ('Connection aborted.', "
            "ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。'))"
        )
    )
    assert service.is_media_stream_blocked_error(
        RuntimeError("ERROR: [download] Got error: Failed to perform, curl: (35) Recv failure: Connection was reset.")
    )
    assert service.is_media_stream_blocked_error(RuntimeError("The read operation timed out"))
    assert service.is_media_stream_blocked_error(RuntimeError("Remote end closed connection without response"))
    assert service.is_media_stream_blocked_error(RuntimeError("IncompleteRead(123 bytes read)"))
    assert not service.is_media_stream_blocked_error(RuntimeError("Requested format is not available."))


def test_youtube_auth_blocked_detection_handles_bot_challenge(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.is_youtube_auth_blocked_error(
        RuntimeError("ERROR: [youtube] Nbwv5wHQoj0: Sign in to confirm you're not a bot.")
    )
    assert service.is_youtube_auth_blocked_error(
        RuntimeError("ERROR: [youtube] Nbwv5wHQoj0: Sign in to confirm you’re not a bot.")
    )
    assert service.is_youtube_auth_blocked_error(
        RuntimeError("ERROR: [youtube] Nbwv5wHQoj0: Sign in to confirm your age.")
    )
    # 提取阶段被要求登录，但消息里完全没有 "--cookies" 提示时也必须认出来
    assert service.is_youtube_auth_blocked_error(RuntimeError("ERROR: [youtube] abc: LOGIN_REQUIRED"))
    assert not service.is_youtube_auth_blocked_error(RuntimeError("Requested format is not available."))


def test_should_try_next_profile_covers_blocked_stream_and_bot_challenge(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.should_try_next_profile(
        RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    )
    assert service.should_try_next_profile(
        RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot.")
    )
    assert not service.should_try_next_profile(RuntimeError("Requested format is not available."))
    assert not service.should_try_next_profile(RuntimeError("unable to write file: permission denied"))


def test_download_escalates_to_anti403_profile_on_bot_challenge(monkeypatch, tmp_path: Path) -> None:
    """bot 校验不是 403，但同样应该触发 profile 阶梯（否则永远用不到 PO token）。"""

    service = YtDlpService(download_dir=tmp_path)
    attempts: list[str] = []

    def fake_download_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, youtube_profile):
        attempts.append(youtube_profile)
        if youtube_profile != "mweb_pot_chrome":
            raise RuntimeError("ERROR: [youtube] abc: Sign in to confirm you're not a bot.")

    monkeypatch.setattr(service, "_download_once", fake_download_once)

    service.download(
        "https://youtu.be/botcheck",
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        progress_hook=lambda payload: None,
        should_cancel=lambda: False,
    )

    assert attempts == ["default", "mweb_pot_chrome"]


def test_download_still_fails_fast_on_unrelated_error(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    attempts: list[str] = []

    def fake_download_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, youtube_profile):
        attempts.append(youtube_profile)
        raise RuntimeError("Requested format is not available.")

    monkeypatch.setattr(service, "_download_once", fake_download_once)

    with pytest.raises(RuntimeError):
        service.download(
            "https://youtu.be/missing-format",
            DownloadOptions(mode="video_subtitles", resolution="720p"),
            progress_hook=lambda payload: None,
            should_cancel=lambda: False,
        )

    assert attempts == ["default"]


def test_default_download_options_do_not_force_anti403_profile(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    assert "impersonate" not in opts
    assert opts.get("extractor_args", {}).get("youtube", {}).get("player_client") != ["web_safari", "default"]


def test_download_options_apply_resolved_system_proxy(monkeypatch, tmp_path: Path) -> None:
    """系统代理必须真的进 ydl_opts —— 否则「浏览器能上网、应用不能」还是会发生。"""

    monkeypatch.setattr("app.proxy.read_system_proxy", lambda: "127.0.0.1:7890")
    monkeypatch.setattr("app.proxy.read_environment_proxy", lambda: None)
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    # 注册表里存的是不带 scheme 的写法，必须补成合法 URL。
    assert opts["proxy"] == "http://127.0.0.1:7890"


def test_download_options_omit_proxy_when_nothing_is_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.proxy.read_system_proxy", lambda: None)
    monkeypatch.setattr("app.proxy.read_environment_proxy", lambda: None)
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    assert "proxy" not in opts


def test_explicit_direct_proxy_is_written_to_download_options(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.proxy.read_system_proxy", lambda: "127.0.0.1:7890")
    monkeypatch.setattr("app.proxy.read_environment_proxy", lambda: None)
    service = YtDlpService(download_dir=tmp_path, proxy="direct")
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    assert opts["proxy"] == ""


def test_extract_metadata_applies_resolved_proxy(monkeypatch, tmp_path: Path) -> None:
    """元数据提取同样是网络请求，也必须带代理（否则解析链接这一步就卡住）。"""

    captured_opts: dict = {}
    monkeypatch.setattr("app.proxy.read_system_proxy", lambda: "127.0.0.1:7890")
    monkeypatch.setattr("app.proxy.read_environment_proxy", lambda: None)

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "X", "webpage_url": url, "formats": []}

    monkeypatch.setattr("app.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)

    YtDlpService(download_dir=tmp_path).extract_metadata("https://youtu.be/x")

    assert captured_opts["proxy"] == "http://127.0.0.1:7890"


def test_prepare_download_reports_unselectable_instead_of_raising(monkeypatch, tmp_path: Path) -> None:
    """yt-dlp 把「选不出格式」实现成**抛异常**；预检必须把它归一成 is_selectable=False。

    这是 ai/bug-fix/010 的回归测试。此前这个异常直接逃逸到 `JobManager`，而
    `_prepare_download` 的降级分支只认「返回 is_selectable=False」，
    于是「不可选就降一级重试」整段成为死代码 —— 界面说「已自动降级到 1080p」，
    实际一个字节都没下。
    """

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            raise DownloadError(
                "ERROR: [youtube] x: Requested format is not available. "
                "Use --list-formats for a list of available formats"
            )

    monkeypatch.setattr("app.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    preparation = service.prepare_download(
        "https://youtu.be/x",
        DownloadOptions(mode="video_subtitles", resolution="1440p"),
    )

    assert preparation.is_selectable is False


def test_prepare_download_reraises_errors_that_are_not_about_format_selection(
    monkeypatch, tmp_path: Path
) -> None:
    """真实故障（网络 / JS challenge / cookies）必须继续抛出，不能被降级逻辑吞掉。"""

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            raise DownloadError("ERROR: [youtube] x: The page needs to be reloaded.")

    monkeypatch.setattr("app.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    with pytest.raises(DownloadError, match="page needs to be reloaded"):
        service.prepare_download(
            "https://youtu.be/x",
            DownloadOptions(mode="video_subtitles", resolution="1440p"),
        )


def test_dependency_status_exposes_proxy_source_and_redacts_credentials(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.proxy.read_system_proxy", lambda: "127.0.0.1:7890")
    monkeypatch.setattr("app.proxy.read_environment_proxy", lambda: "http://user:pw@127.0.0.1:54109")

    status = YtDlpService(download_dir=tmp_path).get_dependency_status()

    assert status["proxy"] == "http://127.0.0.1:7890"
    assert status["proxy_source"] == "system"
    assert status["proxy_writes_ydl_option"] is True
    assert status["environment_proxy"] == "http://user:***@127.0.0.1:54109"


def test_mweb_pot_chrome_download_options_use_provider_and_stability_profile(monkeypatch, tmp_path: Path) -> None:
    browser_path = str(tmp_path / "chrome.exe")
    service = YtDlpService(download_dir=tmp_path)
    service.youtube_po_browser_path = browser_path
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        youtube_profile="mweb_pot_chrome",
    )

    assert isinstance(opts["impersonate"], ImpersonateTarget)
    assert str(opts["impersonate"]) == "chrome"
    assert opts["extractor_args"]["youtube"]["player_client"] == ["mweb", "default"]
    assert opts["extractor_args"]["youtubepot-wpc"]["browser_path"] == [browser_path]
    assert opts["http_chunk_size"] == 16 * 1024 * 1024
    with yt_dlp.YoutubeDL(opts):
        pass


def test_po_token_browser_path_prefers_configured_over_detected(monkeypatch, tmp_path: Path) -> None:
    configured = str(tmp_path / "chrome.exe")
    service = YtDlpService(download_dir=tmp_path, youtube_po_browser_path=configured)
    monkeypatch.setattr(service, "_detect_chromium_executable", lambda: str(tmp_path / "msedge.exe"))

    assert service._po_token_browser_path() == configured


def test_po_token_provider_args_fall_back_to_detected_chromium(monkeypatch, tmp_path: Path) -> None:
    """未显式配置时也必须把探测到的浏览器交给 wpc，否则 provider 是 unavailable。"""

    detected = str(tmp_path / "msedge.exe")
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))
    monkeypatch.setattr(service, "_detect_chromium_executable", lambda: detected)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        youtube_profile="mweb_pot_chrome",
    )

    assert opts["extractor_args"]["youtubepot-wpc"]["browser_path"] == [detected]


def test_po_token_provider_args_absent_when_no_browser_is_found(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))
    monkeypatch.setattr(service, "_detect_chromium_executable", lambda: None)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        youtube_profile="mweb_pot_chrome",
    )

    assert "youtubepot-wpc" not in opts["extractor_args"]


def test_detect_chromium_executable_prefers_path_then_install_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ytdlp_service.shutil,
        "which",
        lambda name: f"C:/found/{name}.exe" if name == "chrome" else None,
    )
    assert ytdlp_service.detect_chromium_executable() == "C:/found/chrome.exe"

    monkeypatch.setattr(ytdlp_service.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")

    assert ytdlp_service.detect_chromium_executable() == str(edge)


def test_detect_node_executable_falls_back_to_install_dir(monkeypatch, tmp_path: Path) -> None:
    node = tmp_path / "nodejs" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    monkeypatch.setattr(ytdlp_service.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing-too"))

    assert ytdlp_service.detect_node_executable() == str(node)


def test_detect_js_runtime_prefers_explicit_path(monkeypatch, tmp_path: Path) -> None:
    deno = tmp_path / "deno.exe"
    deno.write_bytes(b"")
    service = YtDlpService(download_dir=tmp_path, js_runtime_path=str(deno))
    monkeypatch.setattr(service, "_runtime_version", lambda executable: "deno 2.1.0")
    monkeypatch.setattr(ytdlp_service, "detect_node_executable", lambda: None)
    reset_js_runtime_probe(monkeypatch, service)

    assert service._detect_js_runtime() == ("deno", str(deno), "deno 2.1.0")


def test_detect_js_runtime_falls_back_to_install_dir_when_path_is_empty(monkeypatch, tmp_path: Path) -> None:
    """服务进程 PATH 里没有 node 时，也要能从安装目录里找出来。"""

    node = tmp_path / "nodejs" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    monkeypatch.setattr(ytdlp_service.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "missing-too"))
    monkeypatch.setattr(ytdlp_service, "detect_deno_executable", lambda: None)
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_runtime_version", lambda executable: "v22.22.2")
    reset_js_runtime_probe(monkeypatch, service)

    assert service._detect_js_runtime() == ("node", str(node), "v22.22.2")


def test_detect_js_runtime_ignores_unsupported_node_version(monkeypatch, tmp_path: Path) -> None:
    node = str(tmp_path / "node.exe")
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_runtime_version", lambda executable: "v18.20.4")
    monkeypatch.setattr(ytdlp_service, "detect_deno_executable", lambda: None)
    monkeypatch.setattr(ytdlp_service, "detect_node_executable", lambda: node)
    reset_js_runtime_probe(monkeypatch, service)

    assert service._detect_js_runtime() is None


def test_detect_js_runtime_skips_a_candidate_that_cannot_actually_run(monkeypatch, tmp_path: Path) -> None:
    """「文件存在」不等于「能跑」：自检失败的候选必须被跳过，让下一个候选顶上。"""

    broken = str(tmp_path / "broken-node.exe")
    working = str(tmp_path / "working-node.exe")
    # js_runtime_path 是第一个候选，让它坏掉；PATH 探测出来的 node 是好的。
    service = YtDlpService(download_dir=tmp_path, js_runtime_path=broken)
    monkeypatch.setattr(service, "_runtime_version", lambda executable: "v22.22.2")
    monkeypatch.setattr(ytdlp_service, "detect_deno_executable", lambda: None)
    monkeypatch.setattr(ytdlp_service, "detect_node_executable", lambda: working)
    service.reset_js_runtime_cache()
    monkeypatch.setattr(
        service,
        "_probe_js_runtime",
        lambda name, executable: "ERR_ACCESS_DENIED" if executable == broken else None,
    )

    assert service._detect_js_runtime() == ("node", working, "v22.22.2")


def test_js_runtime_probe_reports_the_node_error_verbatim(monkeypatch, tmp_path: Path) -> None:
    """把 node 的原始报错原样带出来 —— 这正是「检测到 node 却解不出 n challenge」的取证。"""

    service = YtDlpService(download_dir=tmp_path)
    captured: list[list[str]] = []

    class Completed:
        returncode = 1
        stdout = ""
        stderr = (
            "Error: Access to this API has been restricted. Use --allow-fs-read to manage permissions.\n"
            "    at Object.readFileSync (node:fs:440:20)\n"
        )

    def fake_run(command, **kwargs):
        captured.append(command)
        return Completed()

    monkeypatch.setattr(ytdlp_service.subprocess, "run", fake_run)

    failure = service._probe_js_runtime("node", "C:/fake/node.exe")

    assert failure is not None
    assert "ERR_ACCESS_DENIED" not in failure  # 报的是原文，不是我们编的 code
    assert "Access to this API has been restricted" in failure
    # 必须按 yt-dlp 的方式（带 --experimental-permission）先试一次。
    assert captured and "--experimental-permission" in captured[0]


def test_js_runtime_probe_never_raises(monkeypatch, tmp_path: Path) -> None:
    """自检本身出错只能算「候选不可用」，绝不能把服务带崩。"""

    service = YtDlpService(download_dir=tmp_path)

    def exploding_run(*args, **kwargs):
        raise AssertionError("subprocess has been replaced by the host environment")

    monkeypatch.setattr(ytdlp_service.subprocess, "run", exploding_run)

    failure = service._probe_js_runtime("node", "C:/fake/node.exe")

    assert failure is not None
    assert "AssertionError" in failure


def test_dependency_status_exposes_js_runtime_failure_reason(monkeypatch, tmp_path: Path) -> None:
    """诊断里必须能说明「为什么运行时不可用」，而不只是 js_runtime=False。"""

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(ytdlp_service, "detect_deno_executable", lambda: None)
    monkeypatch.setattr(ytdlp_service, "detect_node_executable", lambda: "C:/fake/node.exe")
    monkeypatch.setattr(service, "_runtime_version", lambda executable: "v22.22.2")
    reset_js_runtime_probe(monkeypatch, service, failure="启动失败：ERR_ACCESS_DENIED")

    status = service.get_dependency_status()

    assert status["js_runtime"] is False
    assert "ERR_ACCESS_DENIED" in str(status["js_runtime_error"])
    assert status["js_runtime_candidates_rejected"]


def test_safari_hls_download_options_use_safari_profile_accepted_by_ytdlp(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        youtube_profile="safari_hls",
    )

    assert isinstance(opts["impersonate"], ImpersonateTarget)
    assert str(opts["impersonate"]) == "safari"
    assert opts["extractor_args"]["youtube"]["player_client"] == ["web_safari", "default"]
    assert opts["http_chunk_size"] == 16 * 1024 * 1024
    assert opts["format"].startswith("b[height=720][protocol^=m3u8]/")
    with yt_dlp.YoutubeDL(opts):
        pass


def test_default_download_options_use_stability_defaults(tmp_path: Path) -> None:
    options = DownloadOptions()
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(options, cookies_path=None)

    assert options.retries == 10
    assert opts["retries"] == 10
    assert opts["http_chunk_size"] == 16 * 1024 * 1024
    assert "throttledratelimit" not in opts


def test_download_options_enable_resumable_stable_retry_defaults(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best", retries=3, skip_existing=False),
        cookies_path=None,
    )

    assert opts["continuedl"] is True
    assert opts["overwrites"] is True
    assert opts["fragment_retries"] == 20
    assert opts["file_access_retries"] == 5
    assert opts["extractor_retries"] == 5
    assert opts["socket_timeout"] == 30
    assert opts["concurrent_fragment_downloads"] == 1
    assert opts["http_chunk_size"] == 16 * 1024 * 1024
    assert "throttledratelimit" not in opts
    retry_sleep = opts["retry_sleep_functions"]
    assert set(retry_sleep) == {"http", "fragment", "file_access", "extractor"}
    assert retry_sleep["http"](3) > retry_sleep["http"](1)
    assert retry_sleep["http"](n=3) > retry_sleep["http"](n=1)
    assert retry_sleep["http"](1) == 2.0
    assert retry_sleep["http"](20) == 10.0  # 单次等待上限，避免 10 次重试累计成 110s 静默


def test_throttled_rate_is_disabled_by_default(tmp_path: Path) -> None:
    """节流守卫默认关闭：避免 ThrottledDownload 被 yt-dlp 的无计数重提取循环接住（PLAN.md P0-1）。"""
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(DownloadOptions(mode="video_subtitles", resolution="best"), cookies_path=None)

    assert "throttledratelimit" not in opts


def test_throttled_rate_can_be_enabled_explicitly(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path, throttled_rate_kbps=64)

    opts = service.build_download_options(DownloadOptions(mode="video_subtitles", resolution="best"), cookies_path=None)

    assert opts["throttledratelimit"] == 64 * 1024


def test_settings_default_disables_throttled_rate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("YTDL_THROTTLED_RATE_KBPS", raising=False)
    assert AppSettings(download_dir=tmp_path).throttled_rate_kbps == 0


def test_settings_env_can_restore_throttled_rate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("YTDL_THROTTLED_RATE_KBPS", "64")
    service = YtDlpService(
        download_dir=tmp_path,
        throttled_rate_kbps=AppSettings(download_dir=tmp_path).throttled_rate_kbps,
    )

    opts = service.build_download_options(DownloadOptions(mode="video_subtitles", resolution="best"), cookies_path=None)

    assert opts["throttledratelimit"] == 64 * 1024


def test_aria2c_is_not_used_by_default_even_when_available(monkeypatch, tmp_path: Path) -> None:
    aria2c = tmp_path / "aria2c.exe"
    aria2c.write_text("", encoding="utf-8")
    monkeypatch.setattr("app.ytdlp_service.shutil.which", lambda name: str(aria2c) if name == "aria2c" else None)
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
        youtube_profile="default_aria2c",
    )

    assert "external_downloader" not in opts
    assert "external_downloader_args" not in opts


def test_aria2c_profile_uses_conservative_multi_connection_args(tmp_path: Path) -> None:
    aria2c = tmp_path / "aria2c.exe"
    aria2c.write_text("", encoding="utf-8")
    service = YtDlpService(download_dir=tmp_path, aria2c_enabled=True, aria2c_path=str(aria2c))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p", retries=10),
        cookies_path=None,
        youtube_profile="default_aria2c",
    )

    assert opts["external_downloader"] == {"http": str(aria2c), "https": str(aria2c)}
    assert opts["external_downloader_args"]["aria2c"] == [
        "-x",
        "2",
        "-s",
        "2",
        "-j",
        "1",
        "--min-split-size",
        "16M",
        "--max-tries",
        "10",
        "--retry-wait",
        "5",
        "--timeout",
        "30",
        "--connect-timeout",
        "30",
    ]
    assert "impersonate" not in opts


def test_download_tries_optional_aria2c_profile_after_default_media_block(monkeypatch, tmp_path: Path) -> None:
    aria2c = tmp_path / "aria2c.exe"
    aria2c.write_text("", encoding="utf-8")
    service = YtDlpService(download_dir=tmp_path, aria2c_enabled=True, aria2c_path=str(aria2c))
    attempts: list[str] = []

    def fake_download_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, youtube_profile):
        attempts.append(youtube_profile)
        if youtube_profile == "default":
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(service, "_download_once", fake_download_once)

    service.download(
        "https://youtu.be/forbidden",
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        progress_hook=lambda payload: None,
        should_cancel=lambda: False,
    )

    assert attempts == ["default", "default_aria2c"]


def test_po_token_env_config_is_passed_to_youtube_extractor_args(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTDL_YOUTUBE_PO_TOKEN", "TOKEN123")
    monkeypatch.setenv("YTDL_YOUTUBE_VISITOR_DATA", "VISITOR456")
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )
    service = YtDlpService(
        download_dir=tmp_path,
        youtube_po_token=settings.youtube_po_token,
        youtube_visitor_data=settings.youtube_visitor_data,
    )

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["extractor_args"]["youtube"]["po_token"] == ["web.gvs+TOKEN123"]
    assert opts["extractor_args"]["youtube"]["visitor_data"] == ["VISITOR456"]


def test_download_retries_same_resolution_profiles_until_success(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    attempts: list[tuple[str, str]] = []

    def fake_download_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, youtube_profile):
        attempts.append((youtube_profile, options.resolution))
        if youtube_profile != "safari_hls":
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(service, "_download_once", fake_download_once)

    service.download(
        "https://youtu.be/forbidden",
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        progress_hook=lambda payload: None,
        should_cancel=lambda: False,
    )

    assert attempts == [
        ("default", "720p"),
        ("mweb_pot_chrome", "720p"),
        ("safari_hls", "720p"),
    ]


def test_download_tries_chrome_default_profile_last(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    attempts: list[tuple[str, str]] = []

    def fake_download_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, youtube_profile):
        attempts.append((youtube_profile, options.resolution))
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(service, "_download_once", fake_download_once)

    with pytest.raises(RuntimeError):
        service.download(
            "https://youtu.be/forbidden",
            DownloadOptions(mode="video_subtitles", resolution="1080p"),
            progress_hook=lambda payload: None,
            should_cancel=lambda: False,
        )

    assert attempts == [
        ("default", "1080p"),
        ("mweb_pot_chrome", "1080p"),
        ("safari_hls", "1080p"),
        ("chrome_default", "1080p"),
    ]


def test_dependency_status_reports_po_token_provider_without_secret_values(monkeypatch, tmp_path: Path) -> None:
    def fake_version(package_name: str) -> str:
        if package_name == "yt-dlp-getpot-wpc":
            return "1.2.3"
        raise importlib.metadata.PackageNotFoundError(package_name)

    monkeypatch.setattr("app.ytdlp_service.importlib.metadata.version", fake_version)
    service = YtDlpService(
        download_dir=tmp_path,
        youtube_po_token="SECRET_TOKEN",
        youtube_visitor_data="SECRET_VISITOR",
        youtube_po_browser_path=str(tmp_path / "chrome.exe"),
    )

    status = service.get_dependency_status()

    assert status["aria2c_enabled"] is False
    assert status["aria2c_connections"] == 2
    assert "aria2c_available" in status
    assert status["po_token_provider_available"] is True
    assert status["po_token_provider"] == "yt-dlp-getpot-wpc"
    assert status["po_token_provider_version"] == "1.2.3"
    assert status["youtube_po_browser_path_configured"] is True
    assert status["po_token_browser_path"] == str(tmp_path / "chrome.exe")
    assert status["youtube_po_token_configured"] is True
    assert status["youtube_visitor_data_configured"] is True
    assert "SECRET_TOKEN" not in str(status)
    assert "SECRET_VISITOR" not in str(status)


def test_bundled_ffmpeg_is_used_when_system_ffmpeg_is_missing(monkeypatch, tmp_path: Path) -> None:
    bundled_ffmpeg = tmp_path / "bundled-ffmpeg.exe"
    monkeypatch.setattr("app.ytdlp_service.shutil.which", lambda name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled_ffmpeg)),
    )
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="1080p"),
        cookies_path=None,
    )

    assert opts["ffmpeg_location"] == str(bundled_ffmpeg)
    assert service.get_ffmpeg_status() == {"ffmpeg": True, "ffprobe": False}


def test_resolution_can_be_extracted_from_progress_payload_requested_formats(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    resolution = service.resolution_from_progress_payload(
        {
            "status": "finished",
            "info_dict": {
                "requested_formats": [
                    {"format_id": "137", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080},
                    {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "width": None, "height": None},
                ]
            },
        }
    )

    assert resolution == (1920, 1080)


def test_actual_format_can_be_extracted_from_progress_payload_requested_formats(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    actual_format = service.actual_format_from_progress_payload(
        {
            "status": "finished",
            "info_dict": {
                "requested_formats": [
                    {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none"},
                    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                ]
            },
        }
    )

    assert actual_format == "mp4 · avc1 + mp4a"


def test_filesize_sums_requested_formats_for_prepared_download(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    filesize = service._filesize_from_info_dict(
        {
            "requested_formats": [
                {"format_id": "137", "filesize": 12_000},
                {"format_id": "140", "filesize_approx": 3_000},
            ]
        }
    )

    assert filesize == 15_000


def test_suggests_highest_available_resolution_below_requested(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    fallback = service.suggest_lower_resolution(
        "1080p",
        [
            FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
        ],
    )

    assert fallback == "720p"
    assert service.suggest_lower_resolution(
        "1080p",
        [FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4")],
    ) is None
    assert service.suggest_lower_resolution(
        "1440p",
        [
            FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
            FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
        ],
    ) == "1080p"
    assert service.suggest_lower_resolution("720p", []) is None
    assert service.suggest_lower_resolution("720p", [FormatOption(format_id="18", label="360p", height=360)]) is None
    assert service.suggest_lower_resolution("best", [FormatOption(format_id="18", label="360p", height=360)]) is None


def test_suggests_low_resolution_only_when_source_has_no_720_or_higher(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.suggest_lower_resolution(
        "1080p",
        [FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4")],
        allow_below_min_if_source_below_min=True,
    ) == "360p"
    assert service.suggest_lower_resolution(
        "1080p",
        [
            FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
            FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
        ],
        allow_below_min_if_source_below_min=True,
    ) is None


def test_download_options_accept_task_specific_download_dir(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path / "root")
    target_dir = tmp_path / "root" / "Course"
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        download_dir=target_dir,
    )

    assert opts["outtmpl"] == str(target_dir / "%(title).200B [%(id)s].%(ext)s")


def test_subtitle_only_options_skip_video_and_include_languages(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    opts = service.build_download_options(
        DownloadOptions(
            mode="subtitles_only",
            subtitle_languages=["en", "zh-Hans"],
            subtitle_source="both",
            subtitle_format="srt",
        ),
        cookies_path=tmp_path / "cookies.txt",
    )

    assert opts["skip_download"] is True
    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["en", "zh-Hans"]
    assert opts["subtitlesformat"] == "srt"
    assert opts["cookiefile"] == str(tmp_path / "cookies.txt")


def test_empty_subtitle_languages_fall_back_to_bounded_default(tmp_path: Path) -> None:
    """空列表必须回退到有界默认；变成 yt-dlp 的 ["all"] 会拉全部字幕轨并触发 429。"""

    service = YtDlpService(download_dir=tmp_path, default_subtitle_languages=["en"])
    opts = service.build_download_options(DownloadOptions(mode="video_subtitles"), cookies_path=None)

    assert opts["subtitleslangs"] == ["en"]
    assert "all" not in opts["subtitleslangs"]


def test_empty_subtitle_languages_honour_configured_default(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path, default_subtitle_languages=["en", "zh-Hans"])
    opts = service.build_download_options(DownloadOptions(mode="video_subtitles"), cookies_path=None)

    assert opts["subtitleslangs"] == ["en", "zh-Hans"]


def test_empty_subtitle_languages_still_bounded_when_default_is_blank(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path, default_subtitle_languages=[])
    opts = service.build_download_options(DownloadOptions(mode="video_subtitles"), cookies_path=None)

    assert opts["subtitleslangs"] == ["en"]


def test_explicit_subtitle_languages_win_over_default(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path, default_subtitle_languages=["en"])
    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", subtitle_languages=["ja"]),
        cookies_path=None,
    )

    assert opts["subtitleslangs"] == ["ja"]


def test_download_options_default_to_1440p_and_both_subtitle_sources(tmp_path: Path) -> None:
    options = DownloadOptions()
    service = YtDlpService(download_dir=tmp_path)
    opts = service.build_download_options(options, cookies_path=None)

    assert options.resolution == "1440p"
    assert options.subtitle_source == "both"
    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True


def test_explicit_resolution_uses_single_file_selector_without_any_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="1080p"),
        cookies_path=None,
    )

    assert opts["format"] == "b[height=1080][ext=mp4]/b[height=1080]"
    assert "ffmpeg_location" not in opts


def test_download_options_enable_supported_js_runtime(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "get_ffmpeg_status", lambda: {"ffmpeg": False, "ffprobe": False})
    monkeypatch.setattr(service, "_detect_js_runtime", lambda: ("node", "C:/Program Files/nodejs/node.exe", "v20.11.1"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["js_runtimes"] == {"node": {"path": "C:/Program Files/nodejs/node.exe"}}


def test_default_speed_limit_is_unlimited(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    options = DownloadOptions(mode="video_subtitles", resolution="best")

    opts = service.build_download_options(
        options,
        cookies_path=None,
    )

    assert options.speed_limit_kbps is None
    assert "ratelimit" not in opts


def test_explicit_speed_limit_sets_yt_dlp_ratelimit(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best", speed_limit_kbps=2048),
        cookies_path=None,
    )

    assert opts["ratelimit"] == 2048 * 1024


def test_import_browser_cookies_saves_only_youtube_related_cookies(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    jar.set_cookie(_cookie(".google.com", "SID", "GOOGLE_SECRET"))
    jar.set_cookie(_cookie(".example.com", "SESSION", "UNRELATED_SECRET"))
    attempted: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempted.append(browser_name)
        if browser_name == "edge":
            raise RuntimeError("edge is not available")
        return jar

    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    result = YtDlpService(download_dir=tmp_path).import_browser_cookies("auto", tmp_path / "cookies.txt")

    content = (tmp_path / "cookies.txt").read_text(encoding="utf-8")
    assert result.browser == "chrome"
    assert result.imported_count == 2
    assert attempted[:2] == ["edge", "chrome"]
    assert "YOUTUBE_SECRET" in content
    assert "GOOGLE_SECRET" in content
    assert "UNRELATED_SECRET" not in content


def test_import_browser_cookies_reports_locked_edge_database(monkeypatch, tmp_path: Path) -> None:
    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")

    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        YtDlpService(download_dir=tmp_path).import_browser_cookies("edge", tmp_path / "cookies.txt")

    error = exc_info.value
    assert error.code == "browser_locked"
    assert error.browser == "edge"
    assert "Edge" in error.message
    assert not (tmp_path / "cookies.txt").exists()


def test_import_browser_cookies_closes_edge_and_retries_when_allowed(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    attempts: list[str] = []
    closed: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempts.append(browser_name)
        if len(attempts) == 1:
            raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
        return jar

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)
    monkeypatch.setattr(service, "_close_browser_for_cookie_import", lambda browser: closed.append(browser), raising=False)

    result = service.import_browser_cookies("edge", tmp_path / "cookies.txt", close_browser_if_locked=True)

    assert result.browser == "edge"
    assert result.imported_count == 1
    assert attempts == ["edge", "edge"]
    assert closed == ["edge"]
    assert "YOUTUBE_SECRET" in (tmp_path / "cookies.txt").read_text(encoding="utf-8")


def test_import_browser_cookies_uses_edge_cdp_fallback_after_dpapi_failure(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    cdp_calls: list[bool] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        raise RuntimeError("Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)
    monkeypatch.setattr(service, "_extract_edge_cookies_via_cdp", lambda: cdp_calls.append(True) or jar, raising=False)

    result = service.import_browser_cookies("edge", tmp_path / "cookies.txt")

    assert result.browser == "edge"
    assert result.imported_count == 1
    assert cdp_calls == [True]
    assert "YOUTUBE_SECRET" in (tmp_path / "cookies.txt").read_text(encoding="utf-8")


def test_edge_cdp_fallback_never_launches_a_browser(monkeypatch, tmp_path: Path) -> None:
    """CDP 回退不得再启动任何进程：旧实现会挂载**真实** Edge 配置，并破坏它的 cookie 库。"""

    launched: list[tuple] = []

    def forbidden_popen(*args, **kwargs):
        launched.append(args)
        raise AssertionError("CDP fallback must not launch a browser process.")

    # 先把服务构造完再打桩：构造时会合法地跑一次 `node --version` 做运行时自检，
    # 那是产品行为，不该被算成「CDP 启动了进程」。
    service = YtDlpService(download_dir=tmp_path)

    monkeypatch.setattr("app.browser_cookies.subprocess.Popen", forbidden_popen)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        service._extract_edge_cookies_via_cdp()

    assert exc_info.value.code == "edge_app_bound"
    assert launched == []
    assert "scripts/export_cookies_via_cdp.py" in exc_info.value.message


def test_import_browser_cookies_reports_edge_app_bound_after_dpapi_failure(monkeypatch, tmp_path: Path) -> None:
    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        raise RuntimeError("Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        service.import_browser_cookies("edge", tmp_path / "cookies.txt")

    assert exc_info.value.code == "edge_app_bound"
    assert not (tmp_path / "cookies.txt").exists()


def test_auto_browser_cookie_import_prioritizes_edge_app_bound(monkeypatch, tmp_path: Path) -> None:
    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        if browser_name == "edge":
            raise RuntimeError("Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")
        raise RuntimeError(f"could not find {browser_name} profile")

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        service.import_browser_cookies("auto", tmp_path / "cookies.txt")

    assert exc_info.value.code == "edge_app_bound"


def test_auto_browser_cookie_import_prioritizes_locked_edge_error(monkeypatch, tmp_path: Path) -> None:
    empty_jar = YoutubeDLCookieJar()
    attempts: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempts.append(browser_name)
        if browser_name == "edge":
            raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
        return empty_jar

    monkeypatch.setattr("app.ytdlp_service.AUTO_BROWSER_COOKIE_CANDIDATES", ["edge", "chrome"])
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        YtDlpService(download_dir=tmp_path).import_browser_cookies("auto", tmp_path / "cookies.txt")

    assert exc_info.value.code == "browser_locked"
    assert exc_info.value.browser == "edge"
    assert attempts == ["edge", "chrome"]


def test_extract_metadata_maps_playlist_entries_formats_and_subtitles(monkeypatch, tmp_path: Path) -> None:
    captured_opts = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            assert url == "https://youtube.com/playlist?list=abc"
            assert download is False
            return {
                "_type": "playlist",
                "id": "abc",
                "title": "Course",
                "webpage_url": url,
                "entries": [
                    {
                        "id": "v1",
                        "title": "Intro",
                        "webpage_url": "https://youtube.com/watch?v=v1",
                        "duration": 120,
                        "thumbnail": "https://img/1.jpg",
                    }
                ],
                "formats": [
                    {"format_id": "sb0", "height": 180, "ext": "mhtml", "vcodec": "none", "acodec": "none"},
                    {"format_id": "22", "height": 720, "ext": "mp4", "filesize": 10_000},
                    {"format_id": "137", "height": 1080, "ext": "mp4", "filesize_approx": 20_000},
                ],
                "subtitles": {"en": [{"ext": "vtt"}]},
                "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]},
            }

    monkeypatch.setattr("app.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)

    result = YtDlpService(download_dir=tmp_path).extract_metadata(
        "https://youtube.com/playlist?list=abc",
        cookies_path=tmp_path / "cookies.txt",
    )

    assert captured_opts["cookiefile"] == str(tmp_path / "cookies.txt")
    assert captured_opts["ignoreconfig"] is True
    assert captured_opts["extract_flat"] == "in_playlist"
    assert captured_opts["sleep_interval_requests"] == 1.0
    assert result.is_playlist is True
    assert result.title == "Course"
    assert result.entries[0].title == "Intro"
    assert [fmt.format_id for fmt in result.formats] == ["22", "137"]
    assert [fmt.filesize for fmt in result.formats] == [10_000, 20_000]
    assert result.subtitles[0].language == "en"
    assert result.automatic_subtitles[0].language == "zh-Hans"


def test_download_options_ignore_user_ytdlp_config(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["ignoreconfig"] is True


def test_download_options_pace_player_requests_but_not_intra_item_files(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["sleep_interval_requests"] == 1.0
    assert "sleep_interval" not in opts
    assert "max_sleep_interval" not in opts


def test_cookie_required_error_detection_handles_youtube_bot_challenge(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.is_cookie_required_error(
        RuntimeError(
            "ERROR: [youtube] G9MxNwUoSt0: Sign in to confirm you’re not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )
    )
    assert not service.is_cookie_required_error(RuntimeError("Requested format is not available."))


def _cookie(domain: str, name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )

from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import time

from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser


AUTO_BROWSER_COOKIE_CANDIDATES = ["edge", "chrome", "firefox", "brave", "chromium", "vivaldi", "opera"]
YOUTUBE_COOKIE_DOMAIN_SUFFIXES = ("youtube.com", "google.com")


@dataclass(frozen=True)
class BrowserCookieImportResult:
    browser: str
    imported_count: int
    filename: str


class BrowserCookieImportError(RuntimeError):
    def __init__(self, code: str, browser: str | None, message: str, raw_detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.browser = browser
        self.message = message
        self.raw_detail = raw_detail

    @classmethod
    def browser_locked(cls, browser: str, raw_detail: str | None = None) -> "BrowserCookieImportError":
        if browser == "edge":
            message = "Edge 正在运行，cookies 数据库被锁定。请关闭 Edge 后重试，或确认由应用关闭 Edge 并重新导入。"
        else:
            message = f"{browser} 正在运行，cookies 数据库被锁定。请关闭浏览器后重试。"
        return cls("browser_locked", browser, message, raw_detail)

    @classmethod
    def edge_app_bound(cls, browser: str = "edge", raw_detail: str | None = None) -> "BrowserCookieImportError":
        """Edge 的 cookies 无法被本应用离线读取——给出**可执行**的下一步，而不是再试一次。

        三条依据都是实测：① v20 app-bound 加密的密钥绑定 Edge 二进制本身，离线一律
        ``Failed to decrypt with DPAPI``；② Chromium 硬拒在默认 user-data-dir 上开 CDP
        （``DevTools remote debugging requires a non-default data directory``）；
        ③ 用 junction 绕开该限制后，**真实 cookie 库被清空（53 -> 0）**。
        """
        message = (
            "Edge 的 cookies 是 v20 app-bound 加密，密钥绑定正在运行的 Edge 进程本身，"
            "任何离线程序都无法解密。本应用不会再尝试用 CDP 挂载你的真实 Edge 配置"
            "（该做法在 Chromium 上本就被拒绝，绕过限制的变通手段还会破坏 Edge 的 cookies 数据库）。"
            "请任选其一：① 运行仓库里的 scripts/export_cookies_via_cdp.py —— 会开一个独立配置的"
            "浏览器窗口，首次登录一次即可导出到 data/cookies.txt；"
            "② 用浏览器扩展（如 Get cookies.txt LOCALLY）导出后另存为 data/cookies.txt。"
        )
        return cls("edge_app_bound", browser, message, raw_detail)

    def to_detail(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "browser": self.browser,
            "message": self.message,
            "raw_detail": self.raw_detail,
        }


class BrowserCookieImporter:
    def __init__(
        self,
        candidates: list[str] | None = None,
        extract_browser_cookie_jar: Callable[[str], YoutubeDLCookieJar] | None = None,
        close_browser_for_cookie_import: Callable[[str], None] | None = None,
        extract_edge_cookies_via_cdp: Callable[[], YoutubeDLCookieJar] | None = None,
    ) -> None:
        self.candidates = candidates or AUTO_BROWSER_COOKIE_CANDIDATES
        self.extract_browser_cookie_jar = extract_browser_cookie_jar or self._extract_browser_cookie_jar
        self.close_browser_for_cookie_import = close_browser_for_cookie_import or self._close_browser_for_cookie_import
        self.extract_edge_cookies_via_cdp = extract_edge_cookies_via_cdp or self._extract_edge_cookies_via_cdp

    def import_browser_cookies(
        self,
        browser: str,
        target_path: Path,
        close_browser_if_locked: bool = False,
    ) -> BrowserCookieImportResult:
        candidates = self.candidates if browser == "auto" else [browser]
        errors: list[str] = []
        locked_error: BrowserCookieImportError | None = None
        app_bound_error: BrowserCookieImportError | None = None

        for candidate in candidates:
            try:
                imported = self._extract_browser_cookie_jar_with_fallback(candidate, close_browser_if_locked)
            except BrowserCookieImportError as exc:
                if exc.code == "browser_locked":
                    locked_error = exc
                elif exc.code == "edge_app_bound":
                    app_bound_error = exc
                errors.append(f"{candidate}: {exc.raw_detail or exc.message}")
                continue
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

            filtered = YoutubeDLCookieJar(target_path)
            imported_count = 0
            for cookie in imported:
                if self._is_youtube_related_cookie(cookie.domain):
                    filtered.set_cookie(copy(cookie))
                    imported_count += 1

            if imported_count == 0:
                errors.append(f"{candidate}: no YouTube or Google cookies found")
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            filtered.save(target_path, ignore_discard=True, ignore_expires=True)
            return BrowserCookieImportResult(
                browser=candidate,
                imported_count=imported_count,
                filename=target_path.name,
            )

        # 优先抛出「用户真能照做」的错误：关掉浏览器可能真的成功，
        # 而 app-bound 加密只能改用导出脚本 / 扩展。
        if locked_error:
            raise locked_error
        if app_bound_error:
            raise app_bound_error
        detail = "; ".join(errors) if errors else "no supported browser candidates were available"
        raise RuntimeError(f"Could not import YouTube cookies from browser: {detail}")

    def _extract_browser_cookie_jar(self, browser: str) -> YoutubeDLCookieJar:
        return extract_cookies_from_browser(browser)

    def _extract_browser_cookie_jar_with_fallback(
        self,
        browser: str,
        close_browser_if_locked: bool,
    ) -> YoutubeDLCookieJar:
        try:
            return self.extract_browser_cookie_jar(browser)
        except Exception as exc:
            if self._is_browser_cookie_database_locked(browser, exc):
                if not (close_browser_if_locked and browser == "edge"):
                    raise BrowserCookieImportError.browser_locked(browser, str(exc)) from exc
                self.close_browser_for_cookie_import(browser)
                try:
                    return self.extract_browser_cookie_jar(browser)
                except Exception as retry_exc:
                    if self._is_browser_cookie_database_locked(browser, retry_exc):
                        raise BrowserCookieImportError.browser_locked(browser, str(retry_exc)) from retry_exc
                    if self._is_edge_dpapi_decrypt_error(browser, retry_exc):
                        return self.extract_edge_cookies_via_cdp()
                    raise
            if self._is_edge_dpapi_decrypt_error(browser, exc):
                return self.extract_edge_cookies_via_cdp()
            raise

    def _close_browser_for_cookie_import(self, browser: str) -> None:
        if browser != "edge":
            return
        if os.name != "nt":
            raise BrowserCookieImportError.browser_locked(browser, "Automatic Edge closing is only supported on Windows.")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/IM", "msedge.exe", "/F", "/T"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        time.sleep(1.0)

    def _extract_edge_cookies_via_cdp(self) -> YoutubeDLCookieJar:
        """读取真实 Edge 配置里的 cookies —— **已停用**，一律抛出可执行的错误。

        这里原本用 ``--user-data-dir=<真实 Edge 目录>`` 启动 Edge 再走 CDP 读 cookie。
        该实现同时是「必然失败」和「有破坏性」的：

        - 必然失败：Chromium 拒绝在**默认** user-data-dir 上开 CDP，报
          ``DevTools remote debugging requires a non-default data directory``；
          即便绕开，``--headless=new`` 也拿不到 ``.youtube.com`` 域的鉴权 cookie。
        - 有破坏性：上一轮排查中用 junction 绕开该限制后，**真实 cookie 库被清空
          （53 -> 0）**，靠快照才恢复。

        因此这里不再触碰用户的真实配置，改为把「下一步该做什么」交给调用方展示。
        保留方法名是为了继续作为可注入的接缝（``extract_edge_cookies_via_cdp``），
        将来若要接入基于**独立 profile** 的导出流程，替换这一处即可。
        """
        raise BrowserCookieImportError.edge_app_bound("edge")

    def _is_browser_cookie_database_locked(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "could not copy chrome cookie database" in str(exc).lower()

    def _is_edge_dpapi_decrypt_error(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "failed to decrypt with dpapi" in str(exc).lower()

    def _is_youtube_related_cookie(self, domain: str) -> bool:
        normalized = domain.lower().lstrip(".")
        return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in YOUTUBE_COOKIE_DOMAIN_SUFFIXES)


"""代理可用性自测：把「到底走没走代理」变成一个能点出来的结论。

``app/proxy.py`` 只负责**解析**（该用哪个代理），这里负责**验证**（用了之后通不通）。
两者分开是有意的：解析是纯函数、可离线单测；验证必须联网、结果必须是原始证据
（状态码、耗时、原始异常），不能只给一句「失败」。

探测方式刻意用最朴素的一条 HTTPS 请求，而不是跑一次 yt-dlp：yt-dlp 会自己带一堆
重试、cookie、profile 逻辑，一旦失败根本分不清是代理坏了还是提取器坏了。先用
``https://www.youtube.com/robots.txt``（几百字节、必然存在）确认「TCP/TLS + HTTP 通」，
再让用户去跑真正的下载。
"""

from dataclasses import dataclass, field
import time
import urllib.error
import urllib.request

from .proxy import PROXY_SOURCE_DIRECT, PROXY_SOURCE_NONE, ProxyResolution, redact_proxy_credentials


DEFAULT_PROBE_URL = "https://www.youtube.com/robots.txt"
DEFAULT_TIMEOUT_SECONDS = 15.0
USER_AGENT = "cascade-proxy-check/1.0"


@dataclass(frozen=True)
class ProxyTestResult:
    ok: bool
    source: str
    proxy: str | None
    probe_url: str
    http_status: int | None = None
    elapsed_ms: int = 0
    bytes_read: int = 0
    error: str | None = None
    summary: str = ""
    next_steps: list[str] = field(default_factory=list)

    def to_detail(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "source": self.source,
            "proxy": self.proxy,
            "probe_url": self.probe_url,
            "http_status": self.http_status,
            "elapsed_ms": self.elapsed_ms,
            "bytes_read": self.bytes_read,
            "error": self.error,
            "summary": self.summary,
            "next_steps": self.next_steps,
        }


def build_opener(resolution: ProxyResolution):
    """按解析结果构造 opener。

    ``ProxyHandler({})`` 是**关闭**代理（连同环境变量里的一切 proxy 设置），
    与「不传 handler」完全不同 —— 后者会让 urllib 自己去读环境变量，于是探测结果
    就不再等于产品实际使用的设置。这是这个模块最容易写错的一行。

    一个容易误判的实现细节（已实测）：CPython 的 ``ProxyHandler({})`` **不会**注册任何
    ``*_open`` 方法，而 ``build_opener`` 只把「注册过方法」的 handler 放进 ``opener.handlers``，
    所以空映射的 handler 会被静默丢掉 —— 最终结果是 opener 里**根本没有**代理处理，
    也就等价于「不代理」。语义正确，但不要因为「看不到 ProxyHandler」就以为写错了。
    """
    if resolution.url:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": resolution.url, "https": resolution.url})
        )
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def test_proxy(
    resolution: ProxyResolution,
    probe_url: str = DEFAULT_PROBE_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ProxyTestResult:
    started = time.perf_counter()

    def finish(**kwargs: object) -> ProxyTestResult:
        return ProxyTestResult(
            source=resolution.source,
            # url == "" 是 yt-dlp 的「强制直连」写法；展示层统一收敛成 None，
            # 免得界面上出现一个空的「代理：」。
            proxy=redact_proxy_credentials(resolution.url) or None,
            probe_url=probe_url,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            **kwargs,  # type: ignore[arg-type]
        )

    try:
        request = urllib.request.Request(probe_url, headers={"User-Agent": USER_AGENT})
        with build_opener(resolution).open(request, timeout=timeout) as response:
            body = response.read(65536)
            status = getattr(response, "status", None)
    except urllib.error.HTTPError as exc:
        return finish(
            ok=False,
            http_status=exc.code,
            error=f"HTTP {exc.code} {exc.reason}",
            summary=_failure_summary(resolution, f"服务器返回 HTTP {exc.code}"),
            next_steps=_failure_steps(resolution),
        )
    except Exception as exc:  # noqa: BLE001 - 任何异常都要变成可展示的证据
        return finish(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            summary=_failure_summary(resolution, f"{type(exc).__name__}: {exc}"),
            next_steps=_failure_steps(resolution),
        )

    ok = status == 200 and bool(body)
    if ok:
        summary = (
            f"连通：{probe_url} 返回 HTTP {status}，用时 {int((time.perf_counter() - started) * 1000)} ms"
            f"（代理来源：{resolution.source}，代理：{redact_proxy_credentials(resolution.url) or '直连'}）"
        )
        steps: list[str] = []
    else:
        summary = f"探测到意外的响应：HTTP {status}，{len(body)} 字节"
        steps = _failure_steps(resolution)
    return finish(ok=ok, http_status=status, bytes_read=len(body), summary=summary, next_steps=steps)


def _failure_summary(resolution: ProxyResolution, reason: str) -> str:
    if resolution.source == PROXY_SOURCE_DIRECT:
        return f"强制直连失败：{reason}。如果本机直连本来就被拦，这是预期结果，请改为自动或填写代理地址。"
    if resolution.source == PROXY_SOURCE_NONE:
        return f"直连失败（没有发现任何代理）：{reason}。"
    return f"通过代理 {redact_proxy_credentials(resolution.url)} 访问失败：{reason}。"


def _failure_steps(resolution: ProxyResolution) -> list[str]:
    steps: list[str] = []
    if resolution.source in {PROXY_SOURCE_NONE, PROXY_SOURCE_DIRECT}:
        steps.append("本机需要代理才能访问 YouTube：在设置里填写代理地址（Clash 常见 127.0.0.1:7890，v2rayN 常见 127.0.0.1:10809）")
    else:
        steps.append("确认代理软件正在运行、端口一致；改完回到这里重新检测")
        steps.append("若代理软件实际没开，把设置改成 direct 以强制直连，避免所有请求都在等超时")
    steps.append("浏览器能打开 youtube.com 不代表应用能：浏览器可能走系统代理，而这里用的是设置里的地址")
    return steps

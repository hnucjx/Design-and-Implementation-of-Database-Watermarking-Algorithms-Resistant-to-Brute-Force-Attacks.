"""代理解析：把「应用该用哪个代理」变成显式、可观测、可覆盖的行为。

背景（详见 ``ai/bug-fix/006-proxy-is-not-configurable.md``）：
``ydl_opts`` 原先**不含** ``proxy``，代理完全交给 yt-dlp 自己解析 ——
``YoutubeDL.proxies`` 走 ``urllib.request.getproxies()``，而它的取值顺序是
**环境变量优先于 Windows 系统设置**：``getproxies_environment() or getproxies_registry()``
（``yt_dlp/compat/urllib/request.py``）。对一个本地桌面应用来说这有两个后果：

1. 进程环境里任何一个 ``HTTP_PROXY`` / ``HTTPS_PROXY``（从启动它的 shell / IDE /
   容器继承来的、甚至是别的软件留下的）都会**静默顶掉**用户在 Windows 里配的代理，
   而应用里既看不到、也改不了；
2. 反过来，环境里只设置了 ``NO_PROXY`` 时 ``getproxies_environment()`` 返回
   ``{'no': ...}``，**非空**，于是注册表**根本不会被读**，代理悄悄变成直连。

所以这里把代理**显式解析出来**：默认（``auto``）优先采用 Windows 系统代理
（与浏览器一致），其次环境变量；用户也可以显式指定代理，或用 ``direct`` 强制直连。

一个有意为之的细节：yt-dlp 的 ``proxy`` 参数是**单个 URL，不带绕过列表**
（``utils/networking.select_proxy()`` 只有在 ``proxies`` 中存在 ``no`` 键时才会做
绕过判断）。因此当生效来源是**环境变量**时，我们**不**写 ``ydl_opts``，交给 yt-dlp
自己的解析，以保留 ``NO_PROXY`` 的语义；只有「系统代理 / 显式设置 / 强制直连」
这三种来源才覆盖它（``ProxyResolution.writes_ydl_option``）。
"""

from dataclasses import dataclass
import os
from urllib.parse import urlsplit, urlunsplit


PROXY_AUTO = "auto"
PROXY_SOURCE_SETTING = "setting"
PROXY_SOURCE_SYSTEM = "system"
PROXY_SOURCE_ENVIRONMENT = "environment"
PROXY_SOURCE_DIRECT = "direct"
PROXY_SOURCE_NONE = "none"

# 这些写法表示「强制直连」。**空字符串不算** —— 空值一律视为「未配置（auto）」，
# 因为表单清空、或 ``YTDL_PROXY=`` 都不该意外变成「绕过系统代理」。
DIRECT_PROXY_VALUES = frozenset({"direct", "direct://", "none", "off", "no", "-"})
_AUTO_PROXY_VALUES = frozenset({"", PROXY_AUTO, "system", "default"})
# 会显式写入 ydl_opts 的来源；environment 有意不在其中，见模块 docstring。
_OVERRIDING_SOURCES = frozenset({PROXY_SOURCE_SETTING, PROXY_SOURCE_SYSTEM, PROXY_SOURCE_DIRECT})

_WINDOWS_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
ENVIRONMENT_PROXY_NAMES = ("https_proxy", "http_proxy", "all_proxy")
NO_PROXY_NAMES = ("no_proxy",)


def normalize_proxy_url(value: str) -> str:
    """补全 scheme：``127.0.0.1:7890`` → ``http://127.0.0.1:7890``。

    Windows 注册表里存的通常是**不带 scheme** 的 ``127.0.0.1:7890``，而
    urllib 的 ProxyHandler 需要一个带 scheme 的 URL（否则会解析失败并抛错），
    所以这一步是必需的，不是美化。CPython 的 ``getproxies_registry`` 也做同样的事。
    """
    value = value.strip()
    if not value or "://" in value:
        return value
    return f"http://{value}"


def redact_proxy_credentials(value: str | None) -> str | None:
    """把 ``http://user:pass@host:port`` 里的密码换成 ``***``（用于日志/诊断输出）。"""
    if not value or "@" not in value:
        return value
    parts = urlsplit(value)
    if not parts.username:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, f"{parts.username}:***@{host}", parts.path, parts.query, parts.fragment))


def _environment_value(names: tuple[str, ...]) -> str | None:
    for name in names:
        for key in (name, name.upper()):
            value = os.environ.get(key)
            if value and value.strip():
                return value.strip()
    return None


def _proxy_from_server_value(server: str) -> str | None:
    """把 WinINet 的 ``ProxyServer`` 值变成一个代理 URL。

    它可能是 ``127.0.0.1:7890``（单一代理），也可能是
    ``http=...;https=...;socks=...`` 这种分协议写法（CPython 的
    ``getproxies_registry`` 解析的就是同一份数据）。
    """
    server = server.strip()
    if not server:
        return None
    if "=" not in server:
        return normalize_proxy_url(server)
    entries: dict[str, str] = {}
    for chunk in server.split(";"):
        if "=" not in chunk:
            continue
        scheme, address = chunk.split("=", 1)
        scheme = scheme.strip().lower()
        address = address.strip()
        if scheme and address:
            entries[scheme] = address
    for scheme in ("https", "http", "socks5", "socks", "socks4", "ftp"):
        address = entries.get(scheme)
        if not address:
            continue
        if "://" in address:
            return address
        if scheme.startswith("socks"):
            return f"{scheme}://{address}"
        return normalize_proxy_url(address)
    return None


def read_system_proxy() -> str | None:
    """读取 Windows 系统代理（「Internet 选项 / 设置 → 代理」里的那个）。

    非 Windows 或 ``ProxyEnable=0`` 时返回 ``None``。任何读取失败都当作「没有」，
    绝不让一段坏注册表把下载链路整个搞挂。
    """
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - 只在 os.name == "nt" 时不可达
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_REGISTRY_KEY) as key:
            if not winreg.QueryValueEx(key, "ProxyEnable")[0]:
                return None
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    except (OSError, ValueError, TypeError):
        return None
    return _proxy_from_server_value(server)


def read_environment_proxy() -> str | None:
    value = _environment_value(ENVIRONMENT_PROXY_NAMES)
    return normalize_proxy_url(value) if value else None


def has_no_proxy_bypass() -> bool:
    return bool(_environment_value(NO_PROXY_NAMES))


@dataclass(frozen=True)
class ProxyResolution:
    """代理解析结果。``url`` 是**生效值**，``source`` 说明它从哪来。"""

    url: str | None
    source: str
    system_proxy: str | None = None
    environment_proxy: str | None = None

    @property
    def writes_ydl_option(self) -> bool:
        """是否需要把它显式写进 ``ydl_opts['proxy']``（覆盖 yt-dlp 自身的解析）。"""
        return self.source in _OVERRIDING_SOURCES

    def to_ydl_options(self) -> dict[str, str]:
        # url == "" 是 yt-dlp 的「强制直连」写法（--proxy ""），不能丢。
        if self.writes_ydl_option and self.url is not None:
            return {"proxy": self.url}
        return {}


def resolve_proxy(setting: str | None) -> ProxyResolution:
    """把「用户设置 + 系统/环境」解析成一个确定的结果。

    优先级：显式设置 → Windows 系统代理 → 环境变量。
    （系统代理排在环境变量之前是刻意的：桌面应用应当和浏览器一致，而
    环境变量极易被宿主 shell / IDE 无意注入并静默覆盖系统设置。）

    这里对两个来源再补一次 scheme：它们本身已经做过归一化，但补全这一步是幂等的，
    而漏掉它的代价是 urllib 拿一个 ``127.0.0.1:7890`` 直接抛错——不值得省。
    """
    system_proxy = normalize_proxy_url(read_system_proxy() or "") or None
    environment_proxy = normalize_proxy_url(read_environment_proxy() or "") or None
    context = {"system_proxy": system_proxy, "environment_proxy": environment_proxy}

    explicit = (setting or "").strip()
    if explicit.lower() in DIRECT_PROXY_VALUES:
        return ProxyResolution(url="", source=PROXY_SOURCE_DIRECT, **context)
    if explicit.lower() not in _AUTO_PROXY_VALUES:
        return ProxyResolution(url=normalize_proxy_url(explicit), source=PROXY_SOURCE_SETTING, **context)

    if system_proxy:
        return ProxyResolution(url=system_proxy, source=PROXY_SOURCE_SYSTEM, **context)
    if environment_proxy:
        return ProxyResolution(url=environment_proxy, source=PROXY_SOURCE_ENVIRONMENT, **context)
    return ProxyResolution(url=None, source=PROXY_SOURCE_NONE, **context)

"""把 yt-dlp / 网络的原始异常翻译成「发生了什么 + 该做什么」。

动机很具体：本项目线上最贵的一次排障，最后落在一句

    ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.

这句话既没说病因，也没说下一步；真相是 JS 运行时被宿主环境打坏、n challenge 求解失败。
对本地桌面工具来说，用户看到的就是一行天书。所以这里做一层**纯字符串**的翻译层：

* 只依赖异常链上的文本，不依赖网络，可被单测完全覆盖；
* 每个结论都给出 code（便于日志聚合）、一句中文结论、以及**可执行**的下一步；
* 判定顺序从「最具体」到「最泛」，避免 ``connection`` 之类的泛化词吃掉精确病因。
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from .log_safety import sanitize_log_message


# JS challenge / EJS：这些词一旦出现，几乎总是「JS 运行时没真正跑起来」而不是别的。
JS_CHALLENGE_HINTS = (
    "n challenge",
    "nsig",
    "sig challenge",
    "the page needs to be reloaded",
    "challenge solving failed",
    "javascript runtime",
    "js runtime",
    "ejs",
    "err_access_denied",
    "allow-fs-read",
    "--experimental-permission",
)

COOKIE_HINTS = (
    "sign in to confirm",
    "login_required",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "age-restricted",
    "age restricted",
    "this video is age",
    "members-only",
    "private video",
    "cookies-from-browser",
    "--cookies",
)

PROXY_HINTS = (
    "tunnel connection failed",
    "proxyerror",
    "proxy error",
    "cannot connect to proxy",
    "connection refused",
    "actively refused",
    "积极拒绝",
    "no route to host",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
    "407",
)

BLOCK_HINTS = ("http error 403", "403 forbidden", "connection reset", "connection was reset", "remote end closed")


@dataclass(frozen=True)
class Advice:
    code: str
    summary: str
    detail: str = ""
    next_steps: list[str] = field(default_factory=list)

    def to_detail(self) -> dict[str, object]:
        return {"code": self.code, "summary": self.summary, "detail": self.detail, "next_steps": self.next_steps}

    def to_message(self) -> str:
        steps = "；".join(self.next_steps)
        return f"{self.summary}（{self.detail}）" + (f" 下一步：{steps}" if steps else "")

    def to_log_block(self) -> str:
        lines = [f"诊断: {self.code} —— {self.summary}"]
        if self.detail:
            lines.append(f"      原因: {self.detail}")
        for index, step in enumerate(self.next_steps, start=1):
            lines.append(f"      建议{index}: {step}")
        return "\n".join(lines)


def exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """广度优先展开 ``__cause__`` / ``__context__``，去重且不会因环状引用死循环。"""
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend([current.__cause__, current.__context__])


def _messages(exc: BaseException) -> list[str]:
    return [str(current).lower() for current in exception_chain(exc)]


def _first_match(messages: list[str], hints: tuple[str, ...]) -> str | None:
    for message in messages:
        for hint in hints:
            if hint in message:
                return hint
    return None


def advise(
    exc: BaseException,
    *,
    js_runtime_available: bool | None = None,
    js_runtime_error: str | None = None,
    cookies_configured: bool | None = None,
    proxy_source: str | None = None,
    proxy_url: str | None = None,
) -> Advice | None:
    """返回一条可执行的诊断；无法归类时返回 ``None``（不要硬凑）。"""
    messages = _messages(exc)
    if not messages:
        return None

    js_hint = _first_match(messages, JS_CHALLENGE_HINTS)
    if js_hint:
        detail = f"yt-dlp 报出与 JS challenge 相关的信息（命中 “{js_hint}”）。"
        steps: list[str] = []
        if js_runtime_error:
            detail += f" 当前 JS 运行时自检失败：{sanitize_log_message(js_runtime_error)}"
            steps.append("按诊断里的提示修复 JS 运行时（Deno 或 Node），修复后在设置里显式填写路径并重新自检")
        elif js_runtime_available is False:
            detail += " 当前没有可用的 JS 运行时（未检测到 Deno / Node）。"
            steps.append("安装 Deno 或 Node 18+，或在设置里显式指定 JS 运行时路径")
        else:
            steps.append("在设置里点「运行环境自检」，确认 JS 运行时能真正跑起来（只检测到文件是不够的）")
        if cookies_configured:
            steps.append("这类失败只在带 cookies 的登录取数路径上出现；临时可先「清除 cookies」验证是否与登录态相关")
        steps.append("查看日志文件 data/logs/app.log 中 “js runtime” 相关行，那里有 node/deno 的原始报错")
        return Advice(
            code="js_runtime_challenge_failed",
            summary="YouTube 的 JS challenge（n 参数）解不出来，登录态下的可用格式被判定为空",
            detail=detail,
            next_steps=steps,
        )

    cookie_hint = _first_match(messages, COOKIE_HINTS)
    if cookie_hint:
        detail = f"yt-dlp 要求登录态（命中 “{cookie_hint}”）。"
        state = {True: "已配置", False: "未配置", None: "未知"}[cookies_configured]
        detail += f" 当前 cookies：{state}。"
        return Advice(
            code="cookie_required",
            summary="YouTube 要求登录或人机校验，匿名请求被拒",
            detail=detail,
            next_steps=[
                "在「解析链接」面板点「从浏览器导入」，或运行 python scripts/export_cookies_via_cdp.py 导出 data/cookies.txt",
                "导入后点「校验 cookies」，确认结论是「包含 youtube.com 域下的鉴权 cookie」",
                "若校验通过但仍失败，检查代理是否与导出 cookies 时用的是同一条出口（换 IP 会让登录态失效）",
            ],
        )

    proxy_hint = _first_match(messages, PROXY_HINTS)
    if proxy_hint:
        where = f"当前生效代理：{proxy_url or '<直连>'}（来源：{proxy_source or '未知'}）。"
        return Advice(
            code="proxy_unreachable",
            summary="连不上 YouTube，且错误形态指向代理/网络出口",
            detail=f"命中 “{proxy_hint}”。{where}",
            next_steps=[
                "确认代理软件正在运行，且端口与设置里填的一致（Clash 常见 7890、v2rayN 常见 10808/10809）",
                "在设置里点「检测代理」，看到 HTTP 200 才算通；失败时按提示改成 direct 或填入正确地址",
                "浏览器能打开 youtube.com 不代表应用能：浏览器可能走系统代理而你显式填了别的地址",
            ],
        )

    block_hint = _first_match(messages, BLOCK_HINTS)
    if block_hint:
        return Advice(
            code="media_stream_blocked",
            summary="YouTube 拒绝了媒体流（403 / 连接被重置）",
            detail=f"命中 “{block_hint}”。后台已尝试 PO token provider、impersonation、断点续传与重试。",
            next_steps=[
                "重新导入 cookies（登录态过期是最常见原因）",
                "降低并发到 1、关闭 aria2c，减少触发风控",
                "确认代理出口稳定：中途换 IP 会让已签发的媒体 URL 失效",
            ],
        )

    return None

"""宿主进程环境里会**静默打坏子进程**的变量及其清理。

背景（见 ``ai/bug-fix/007-js-challenge-fails-only-when-cookies-are-on.md``）：
yt-dlp 解 YouTube 的 ``n`` challenge 时会这样启动 JS 运行时 ——

    node --experimental-permission --no-warnings=ExperimentalWarning -

``--experimental-permission`` 会打开 Node 的**权限模型**（默认拒绝一切文件读取）。
而 ``NODE_OPTIONS`` 是**每个 node 进程都会读**的环境变量：只要宿主 shell / IDE /
桌面壳往里面塞了一个 ``--require=<某个 .cjs 补丁>``，那个补丁就会被塞进上面这个
被沙箱化的 node 进程，Node 随即拒绝读取它并退出：

    Error: Access to this API has been restricted. Use --allow-fs-read to manage permissions.
      code: 'ERR_ACCESS_DENIED', permission: 'FileSystemRead',
      resource: '...\\node-language-shim.cjs'

退出码非 0 → n challenge 解不出来 → 部分 client 的格式被判定为「没有可用 URL」→
最终抛出的却是**完全指不到病因**的 ``ERROR: The page needs to be reloaded.``

这类变量不是用户业务配置，而是宿主环境泄漏；对一个本地桌面应用来说，
让它污染下载链路没有任何好处。所以这里在启动时**摘掉**它们，并把动作记进日志与
``/api/diagnostics``，而不是继续静默失败。
"""

from dataclasses import dataclass
import os


# 会把外部代码强加载进每个 node 进程的开关。在权限模型下这些必定失败，
# 在没有权限模型时也只是把无关代码塞进 JS 求解器——两种情况下都该摘掉。
HOSTILE_NODE_OPTION_FLAGS = ("--require", "--import", "--loader", "--experimental-loader")


@dataclass(frozen=True)
class SanitizedVariable:
    name: str
    value: str
    reason: str

    def to_detail(self) -> dict[str, str]:
        return {"name": self.name, "value": self.value, "reason": self.reason}


def hostile_node_options_reason(value: str | None) -> str | None:
    """``NODE_OPTIONS`` 命中强加载开关时返回原因，否则返回 None。"""
    if not value:
        return None
    for flag in HOSTILE_NODE_OPTION_FLAGS:
        if flag in value:
            return (
                f"NODE_OPTIONS 含有 {flag}：该开关会把外部脚本强加载进每个 node 进程，"
                "而 yt-dlp 用 --experimental-permission 启动 node 时会拒绝读取它，"
                "导致 n challenge 求解失败、YouTube 报 “The page needs to be reloaded.”"
            )
    return None


def sanitize_environment(environ: dict[str, str] | None = None) -> list[SanitizedVariable]:
    """摘掉会破坏 JS 运行时的环境变量。返回被摘掉的变量（幂等）。"""
    target = os.environ if environ is None else environ
    removed: list[SanitizedVariable] = []

    reason = hostile_node_options_reason(target.get("NODE_OPTIONS"))
    if reason:
        removed.append(SanitizedVariable(name="NODE_OPTIONS", value=target.get("NODE_OPTIONS", ""), reason=reason))
        target.pop("NODE_OPTIONS", None)

    return removed


def describe_environment_risks(environ: dict[str, str] | None = None) -> list[dict[str, str]]:
    """只读地列出当前仍存在的环境风险（供 /api/diagnostics 展示）。"""
    target = os.environ if environ is None else environ
    risks: list[dict[str, str]] = []
    reason = hostile_node_options_reason(target.get("NODE_OPTIONS"))
    if reason:
        risks.append({"name": "NODE_OPTIONS", "value": target.get("NODE_OPTIONS", ""), "reason": reason})
    return risks


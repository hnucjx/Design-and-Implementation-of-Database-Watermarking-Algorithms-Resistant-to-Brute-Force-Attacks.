"""清晰度降级的**决策**。

拆出来的理由见 [ai/refactor/refactor.md §4 路线图 R3](../../ai/refactor/refactor.md)：降级是本项目历史上
唯一**反复**出缺陷的策略 —— [ai/bug-fix/010](../../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)
里整段降级曾经是死代码（yt-dlp 用抛异常表达"选不出来"，而当时只认 `is_selectable=False`），
而它的判据过去只能靠跑整条下载链路来验。本模块把这四个问题收拢成纯函数：

1. 这种情况值不值得再取一次元数据去找降级候选？—— `should_look_for_fallback`
2. 探测达不到目标清晰度时，降到哪、为什么、降不了时怎么报？—— `decide_probe_fallback`
3. 下载中途被媒体流 403 / 连接重置打断时，要不要标注一个可重启的清晰度？—— `decide_media_stream_fallback`
4. 「Requested format is not available」失败时，要不要标注？—— `decide_unavailable_format_fallback`

**纯到什么程度**：不联网、不读库、不认识 `JobManager`、不改 `JobItem`。可用清晰度由调用方
（`job_manager`）先 `extract_metadata` 拿到再传进来；**取不到就传 `None`**。IO 与时序留在调用方，
正是为了让这里的判定可以被单测。

**`None` 与 `[]` 的分工（如实说明）**：`None` 表示"元数据这次没取到"，`[]` 表示"取到了、但一个格式
都没有"。类型上分开是为了不让调用方把"没取到"顺手写成"没有格式"。但**当前三条判定路径对两者的
结论完全相同**（都按"目标清晰度不可得"处理）—— 这份等价性由
`backend/tests/test_resolution_decisions.py::test_missing_metadata_and_empty_format_list_currently_agree`
钉住。以后若要让它们分道扬镳，那条测试会先失败，逼你回来改这里的文档，而不是让语义悄悄漂移。

依赖方向：L3 → L1（`schemas`）与 L3（`ytdlp_formats` / `fallback_policy`）。不导入 yt-dlp。

**下一步的边界**：本模块只负责"降到哪 / 为什么"。把降级原因翻译成给用户看的整句话仍在
`fallback_policy.build_resolution_fallback`，两者不重复：本模块产出 `reason`，由它产出 `message`。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .fallback_policy import (
    MEDIA_STREAM_BLOCKED,
    REQUESTED_RESOLUTION_MISSING,
    REQUESTED_RESOLUTION_UNSELECTABLE,
    SOURCE_BELOW_720_ONLY,
)
from .schemas import DownloadOptions, FormatOption
from .ytdlp_formats import (
    DEFAULT_MIN_AUTO_FALLBACK_HEIGHT,
    has_resolution_at_or_above,
    resolution_height,
    suggest_lower_resolution,
)


#: 自动降级允许下降到的最低清晰度。低于它只在"源本身就没有更高清晰度"时才允许。
MIN_AUTO_FALLBACK_HEIGHT = DEFAULT_MIN_AUTO_FALLBACK_HEIGHT


class ResolutionDecisionKind(str, Enum):
    """决策的三态。用字符串枚举是为了让日志与失败信息直接可读。"""

    skip = "skip"  #: 不降级（也不需要标注）
    fallback = "fallback"  #: 要降级，`fallback_resolution` 与 `reason` 保证非空
    fail = "fail"  #: 降不了，调用方应把 `message` 报给用户


@dataclass(frozen=True)
class ResolutionDecision:
    """一次降级决策的结果。

    `kind` 决定调用方做什么，其余字段只在对应形态下有意义：
    `fallback` 用 `fallback_resolution` + `reason`（`message` 仅在第 4 条路径上带着，
    因为那条路径要把原因写进 `item.error`）；`fail` 只用 `message`。
    """

    kind: ResolutionDecisionKind
    fallback_resolution: str | None = None
    reason: str | None = None
    message: str | None = None


def should_look_for_fallback(options: DownloadOptions) -> bool:
    """值不值得再取一次元数据去找降级候选。

    两种情况直接返回 `False`（与重构前 `JobManager._prepare_download` 的早退条件逐字一致）：

    1. 用户显式指定了 `format_id` —— 那是精确选择，不该被自动改写；
    2. `resolution` 解析不出高度（`best` 或非法值）—— 没有"比它低"的定义。

    判据落在这里而不是调用方，是因为"该不该找"本身就是降级策略的一部分；
    但它必须是**纯判断**，否则调用方就没法在取元数据之前问这一句。
    """
    if options.format_id:
        return False
    return resolution_height(options.resolution) is not None


def decide_probe_fallback(
    requested_resolution: str,
    analysis_formats: list[FormatOption] | None,
) -> ResolutionDecision:
    """探测确认"目标清晰度选不出可下载组合"之后，该怎么办。

    调用前提：探针已经确认**不可选**（返回 `is_selectable=False`，或抛了
    「Requested format is not available」）。本函数**不重复**判断可选性，只决定降级。

    `analysis_formats is None` 表示元数据没取到（不存在、或取的时候出错）；`[]` 表示取到了但没有任何
    格式。**两者在本函数里结论相同**：都按"目标清晰度不可得"处理，允许降到 720p 以下，因为源可能
    本来就低清。这个等价性由测试钉住（见模块文档），不是巧合。

    三种出口（与重构前逐字等价）：

    - 找到 `>= 720p` 的更低清晰度 → `fallback`，原因按**源里有没有 720p 及以上**区分
      `REQUESTED_RESOLUTION_MISSING` / `SOURCE_BELOW_720_ONLY`；
    - 目标清晰度**在源里存在**但没有可下载组合 → `fallback`，原因
      `REQUESTED_RESOLUTION_UNSELECTABLE`（此时不允许降到 720p 以下）；
    - 降不了 → `fail`，文案按 `height_missing` 选 `no_supported_fallback_message`
      或 `unselectable_resolution_message`。这两句话不能互换：前者是"视频本来就没有"，
      后者是"有但没得下"。
    """
    requested_height = resolution_height(requested_resolution)
    if requested_height is None:
        return ResolutionDecision(ResolutionDecisionKind.skip)

    formats = analysis_formats or []
    available_heights = {int(format.height) for format in formats if format.height is not None}
    height_missing = analysis_formats is None or requested_height not in available_heights

    fallback = suggest_lower_resolution(
        requested_resolution,
        formats,
        min_height=MIN_AUTO_FALLBACK_HEIGHT,
        allow_below_min_if_source_below_min=height_missing,
    )
    if fallback is None:
        message = (
            no_supported_fallback_message(requested_resolution)
            if height_missing
            else unselectable_resolution_message(requested_resolution)
        )
        return ResolutionDecision(ResolutionDecisionKind.fail, message=message)

    if height_missing:
        # 只有 analysis 取到时才可能走到这里（取不到时上游必然 fail），
        # 所以这里问 `formats` 与问 `analysis.formats` 等价。
        reason = (
            SOURCE_BELOW_720_ONLY
            if not has_resolution_at_or_above(formats, min_height=MIN_AUTO_FALLBACK_HEIGHT)
            else REQUESTED_RESOLUTION_MISSING
        )
    else:
        reason = REQUESTED_RESOLUTION_UNSELECTABLE

    return ResolutionDecision(
        ResolutionDecisionKind.fallback,
        fallback_resolution=fallback,
        reason=reason,
    )


def decide_media_stream_fallback(
    requested_resolution: str,
    analysis_formats: list[FormatOption] | None,
) -> ResolutionDecision:
    """媒体流被 YouTube 拒绝（403）或连接重置时的**标注**决策。

    与 `decide_unavailable_format_fallback` 的两点差别都是承重的：

    - **允许降到 720p 以下**（`allow_below_min_if_source_below_min=True`）：媒体流失败与
      "目标清晰度不可选"不同，源本身低清就足以构成降级理由；
    - **不改写 `item.error`**（`message` 恒为 `None`）：这类失败的文案由
      `media_stream_failure_message` 单独给，它要带上 cookies 状态，属于另一件事。

    元数据取不到（`None`）→ `skip`，与原实现一致（原实现在 `extract_metadata` 抛异常时返回 `None`）。
    """
    if analysis_formats is None:
        return ResolutionDecision(ResolutionDecisionKind.skip)

    fallback = suggest_lower_resolution(
        requested_resolution,
        analysis_formats,
        min_height=MIN_AUTO_FALLBACK_HEIGHT,
        allow_below_min_if_source_below_min=True,
    )
    if fallback is None:
        return ResolutionDecision(ResolutionDecisionKind.skip)

    return ResolutionDecision(
        ResolutionDecisionKind.fallback,
        fallback_resolution=fallback,
        reason=MEDIA_STREAM_BLOCKED,
    )


def decide_unavailable_format_fallback(
    requested_resolution: str,
    analysis_formats: list[FormatOption] | None,
) -> ResolutionDecision:
    """`Requested format is not available` 失败时的标注决策。

    此时任务已经 `failed`，用户看到的原因必须能解释"为什么降过级仍然失败" ——
    所以这条路径**要**改 `item.error`（`message` 非空）。

    不允许降到 720p 以下：这种失败的含义是"目标清晰度没有可下载的视频/音频组合"，
    与源的固有清晰度无关。
    """
    if analysis_formats is None:
        return ResolutionDecision(ResolutionDecisionKind.skip)

    fallback = suggest_lower_resolution(
        requested_resolution,
        analysis_formats,
        min_height=MIN_AUTO_FALLBACK_HEIGHT,
        allow_below_min_if_source_below_min=False,
    )
    if fallback is None:
        return ResolutionDecision(ResolutionDecisionKind.skip)

    return ResolutionDecision(
        ResolutionDecisionKind.fallback,
        fallback_resolution=fallback,
        reason=REQUESTED_RESOLUTION_UNSELECTABLE,
        message=resolution_fallback_error_message(requested_resolution, fallback),
    )


def resolution_fallback_error_message(requested_resolution: str, fallback_resolution: str) -> str:
    """目标清晰度存在、但整条下载仍失败时写进 `item.error` 的一句话。"""
    return f"当前没有 {requested_resolution} 的视频，低于选定分辨率的最高可用分辨率是 {fallback_resolution}。"


def no_supported_fallback_message(requested_resolution: str) -> str:
    """降不了的两种原因之一：视频本来就没有这个清晰度，且没有 720p 及以上的更低清晰度。"""
    return (
        f"当前没有 {requested_resolution} 的视频，"
        f"也没有 {MIN_AUTO_FALLBACK_HEIGHT}p 或更高的可用降级清晰度。"
    )


def unselectable_resolution_message(requested_resolution: str) -> str:
    """降不了的两种原因之二：清晰度存在，但没有可下载组合，且没有 720p 及以上的更低清晰度。"""
    return (
        f"检测到 {requested_resolution} 清晰度，但该清晰度当前没有可下载的视频/音频组合，"
        f"也没有 {MIN_AUTO_FALLBACK_HEIGHT}p 或更高的可用降级清晰度。"
    )


def media_stream_failure_message(cookie_state: str) -> str:
    """媒体流失败时的用户文案。

    `cookie_state` 是 `"已配置"` / `"未配置"` 之一，由调用方查文件系统后传入 ——
    本模块不碰 `AppSettings`，也就不会把"读磁盘"藏进一个看起来纯的函数里。
    """
    return (
        f"当前 cookies 状态：{cookie_state}。"
        "YouTube 拒绝了媒体流下载（HTTP 403）或重置了媒体流连接。后台已在当前清晰度下尝试 PO-token provider、"
        "浏览器 impersonation、断点续传和传输重试；请重新导入 cookies 后重试。若浏览器可正常播放但仍失败，"
        "请检查网络/代理是否能稳定访问 YouTube 媒体域名，或配置有效的 YouTube PO token。"
    )

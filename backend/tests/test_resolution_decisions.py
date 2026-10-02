"""`resolution_decisions` 的单元测试。

这是 R3 的产出，也是**降级判据第一次可以脱离下载链路被验证**的地方：在此之前，
要验一条降级规则就得构造一个假 service、再跑一次完整的 item 工作流
（见 `test_api.py::test_unselectable_probe_that_raises_still_auto_falls_back_and_downloads`）。

这里只测**判定**，不测 IO 与时序 —— 后者由 `test_api.py` 的端到端用例守着。
"""

from __future__ import annotations

import pytest

from app.fallback_policy import (
    MEDIA_STREAM_BLOCKED,
    REQUESTED_RESOLUTION_MISSING,
    REQUESTED_RESOLUTION_UNSELECTABLE,
    SOURCE_BELOW_720_ONLY,
    build_resolution_fallback,
)
from app.resolution_decisions import (
    ResolutionDecisionKind,
    decide_media_stream_fallback,
    decide_probe_fallback,
    decide_unavailable_format_fallback,
    media_stream_failure_message,
    no_supported_fallback_message,
    resolution_fallback_error_message,
    should_look_for_fallback,
    unselectable_resolution_message,
)
from app.schemas import DownloadOptions, FormatOption


def formats(*heights: int) -> list[FormatOption]:
    """把一组高度变成 `FormatOption` 列表（顺序即"源里的顺序"，判定不应依赖它）。"""
    return [FormatOption(format_id=str(height), label=f"{height}p", height=height) for height in heights]


# --------------------------------------------------------------------------
# should_look_for_fallback：什么时候**不值得**再取一次元数据
# --------------------------------------------------------------------------


def test_should_not_look_for_fallback_when_format_id_is_explicit() -> None:
    """用户显式指定 format_id 是精确选择，不该被自动改写。"""
    options = DownloadOptions(resolution="1080p", format_id="137")
    assert should_look_for_fallback(options) is False


@pytest.mark.parametrize("resolution", ["best", "hd", "1080", ""])
def test_should_not_look_for_fallback_when_resolution_has_no_height(resolution: str) -> None:
    """解析不出高度就没有"比它低"的定义，连元数据都不必取。"""
    assert should_look_for_fallback(DownloadOptions(resolution=resolution)) is False


def test_should_look_for_fallback_on_a_concrete_height() -> None:
    assert should_look_for_fallback(DownloadOptions(resolution="1080p")) is True


# --------------------------------------------------------------------------
# decide_probe_fallback：探针确认「选不出可下载组合」之后
# --------------------------------------------------------------------------


def test_probe_fallback_fails_when_metadata_is_unavailable() -> None:
    """元数据取不到 → 降不了，且文案是「本来就没有」那一句，不是「有但没得下」。"""
    decision = decide_probe_fallback("1080p", None)

    assert decision.kind is ResolutionDecisionKind.fail
    assert decision.fallback_resolution is None
    assert decision.message == no_supported_fallback_message("1080p")
    assert "720p" in decision.message


def test_probe_fallback_picks_lower_height_when_source_lacks_requested() -> None:
    """源里没有 1080p，但有 720p → 降到 720p，原因是「本来没有」。"""
    decision = decide_probe_fallback("1080p", formats(1440, 720, 480))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.fallback_resolution == "720p"
    assert decision.reason == REQUESTED_RESOLUTION_MISSING
    assert decision.message is None


def test_probe_fallback_allows_below_720_when_source_is_low_only() -> None:
    """源本身全都低于 720p → 允许跌破 720，并给出 source_below_720_only。"""
    decision = decide_probe_fallback("1080p", formats(480, 360))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.fallback_resolution == "480p"
    assert decision.reason == SOURCE_BELOW_720_ONLY


def test_probe_fallback_fails_when_requested_exists_but_no_safe_lower_height() -> None:
    """目标清晰度**存在**、但没有 720p 及以上的更低档 → 不许跌破 720，只能失败。"""
    decision = decide_probe_fallback("1080p", formats(1080, 480))

    assert decision.kind is ResolutionDecisionKind.fail
    assert decision.message == unselectable_resolution_message("1080p")


def test_probe_fallback_marks_unselectable_when_requested_exists_with_safe_lower_height() -> None:
    """目标清晰度存在、也有 720p 更低档 → 降级，原因是「有但没得下」。"""
    decision = decide_probe_fallback("1080p", formats(1080, 720))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.fallback_resolution == "720p"
    assert decision.reason == REQUESTED_RESOLUTION_UNSELECTABLE


def test_probe_fallback_picks_the_highest_safe_lower_height() -> None:
    """降级要取**最高的**安全档，而不是最近邻或最低档。"""
    decision = decide_probe_fallback("2160p", formats(2160, 1440, 1080, 720, 360))

    assert decision.fallback_resolution == "1440p"


def test_probe_fallback_skips_when_resolution_is_not_a_height() -> None:
    decision = decide_probe_fallback("best", formats(1080, 720))

    assert decision.kind is ResolutionDecisionKind.skip


def test_probe_fallback_ignores_formats_without_height() -> None:
    """`height` 为空的格式不能参与降级候选计算（音频流就没有高度）。"""
    analysis = [FormatOption(format_id="140", label="audio", height=None), *formats(720)]

    assert decide_probe_fallback("1080p", analysis).fallback_resolution == "720p"


def test_probe_failure_messages_are_not_interchangeable() -> None:
    """两句失败文案不能互换：「本来没有」与「有但没得下」对用户是两回事。"""
    missing = decide_probe_fallback("1080p", formats(1080, 480)).message
    unselectable = decide_probe_fallback("1080p", None).message

    assert missing != unselectable
    assert missing.startswith("检测到 1080p 清晰度")
    assert unselectable.startswith("当前没有 1080p 的视频")


# --------------------------------------------------------------------------
# decide_media_stream_fallback：媒体流 403 / 连接重置
# --------------------------------------------------------------------------


def test_media_stream_fallback_skips_when_metadata_is_unavailable() -> None:
    """元数据取不到就不标注 —— 不能凭空标一个没验证过的清晰度。"""
    decision = decide_media_stream_fallback("1080p", None)

    assert decision.kind is ResolutionDecisionKind.skip


def test_media_stream_fallback_may_drop_below_720_for_low_source() -> None:
    """媒体流失败时允许跌破 720：源本来低清就足以构成降级理由。"""
    decision = decide_media_stream_fallback("1080p", formats(360))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.fallback_resolution == "360p"
    assert decision.reason == MEDIA_STREAM_BLOCKED


def test_media_stream_fallback_does_not_touch_the_error_message() -> None:
    """这条路不改写 `item.error`：媒体流失败的文案要带 cookies 状态，是另一件事。"""
    decision = decide_media_stream_fallback("1080p", formats(1080, 720))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.message is None


def test_media_stream_fallback_skips_when_there_is_nothing_lower() -> None:
    assert decide_media_stream_fallback("720p", formats(720)).kind is ResolutionDecisionKind.skip


# --------------------------------------------------------------------------
# decide_unavailable_format_fallback：「Requested format is not available」
# --------------------------------------------------------------------------


def test_unavailable_format_fallback_annotates_with_a_new_error_message() -> None:
    decision = decide_unavailable_format_fallback("1080p", formats(1080, 720))

    assert decision.kind is ResolutionDecisionKind.fallback
    assert decision.fallback_resolution == "720p"
    assert decision.reason == REQUESTED_RESOLUTION_UNSELECTABLE
    assert decision.message == resolution_fallback_error_message("1080p", "720p")


def test_unavailable_format_fallback_never_drops_below_720() -> None:
    """「目标清晰度没有可下载组合」与源的固有清晰度无关，所以不许跌破 720。"""
    decision = decide_unavailable_format_fallback("1080p", formats(1080, 480))

    assert decision.kind is ResolutionDecisionKind.skip


def test_unavailable_format_fallback_skips_without_metadata() -> None:
    assert decide_unavailable_format_fallback("1080p", None).kind is ResolutionDecisionKind.skip


# --------------------------------------------------------------------------
# 「元数据没取到」与「取到了但没有格式」当前等价 —— 这条等价性要被钉住
# --------------------------------------------------------------------------


@pytest.mark.parametrize("resolution", ["1080p", "720p", "best"])
def test_missing_metadata_and_empty_format_list_currently_agree(resolution: str) -> None:
    """`None`（没取到）与 `[]`（取到了但没有格式）在三条判定路径上结论相同。

    这不是"反正一样"的随口断言，而是一条**刻意的栅栏**：两者在类型上是不同的事实，
    如果将来有人让它们分道扬镳（例如"源确实没有格式 → 直接失败"，而不是去降级），
    这条用例会先失败，逼他回来更新 `resolution_decisions` 的模块文档与 `design.md`，
    而不是让语义悄悄改掉。
    """
    for decide in (
        decide_probe_fallback,
        decide_media_stream_fallback,
        decide_unavailable_format_fallback,
    ):
        assert decide(resolution, None) == decide(resolution, []), decide.__name__


# --------------------------------------------------------------------------
# 文案
# --------------------------------------------------------------------------


def test_media_stream_failure_message_reports_the_cookie_state_it_is_given() -> None:
    unconfigured = media_stream_failure_message("未配置")
    configured = media_stream_failure_message("已配置")

    assert "当前 cookies 状态：未配置。" in unconfigured
    assert "当前 cookies 状态：已配置。" in configured
    # 除 cookies 状态外必须逐字相同，否则说明这段文案被改成了两个分支的复制品。
    assert unconfigured.replace("未配置", "X") == configured.replace("已配置", "X")


# --------------------------------------------------------------------------
# 与 fallback_policy 的接缝
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        REQUESTED_RESOLUTION_MISSING,
        SOURCE_BELOW_720_ONLY,
        REQUESTED_RESOLUTION_UNSELECTABLE,
        MEDIA_STREAM_BLOCKED,
    ],
)
def test_every_reason_we_emit_is_translatable_to_a_user_message(reason: str) -> None:
    """本模块产出的 reason 必须都能被 `fallback_policy` 翻成非空文案。

    防的是「新增了一个 reason 常量，却忘了在文案表里加分支」—— 那时
    `build_resolution_fallback` 会落到兜底分支，给出与原因不符的解释。
    """
    fallback = build_resolution_fallback("1080p", "720p", status="failed", reason=reason)

    assert fallback is not None
    assert fallback.message
    assert fallback.reason == reason


def test_reasons_we_emit_are_distinguishable_by_their_message_and_restart_hint() -> None:
    """四个 reason 的「文案 + 重启建议」组合两两不同。"""
    seen = {
        (fb.message, fb.restart_resolution)
        for fb in (
            build_resolution_fallback("1080p", "720p", status="failed", reason=reason)
            for reason in (
                REQUESTED_RESOLUTION_MISSING,
                SOURCE_BELOW_720_ONLY,
                REQUESTED_RESOLUTION_UNSELECTABLE,
                MEDIA_STREAM_BLOCKED,
            )
        )
    }

    assert len(seen) == 4

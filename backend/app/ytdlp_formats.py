from typing import Any

from .schemas import DownloadOptions, FormatOption


DEFAULT_MIN_AUTO_FALLBACK_HEIGHT = 720


def format_selector(options: DownloadOptions, allow_merge: bool = True, prefer_hls: bool = False) -> str:
    if not allow_merge:
        return single_file_format_selector(options)
    if options.format_id:
        return f"{options.format_id}+ba/{options.format_id}"
    if options.resolution == "best":
        return "b[protocol^=m3u8]/bv*+ba/b" if prefer_hls else "bv*+ba/b"
    if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
        height = int(options.resolution[:-1])
        selector = (
            f"bv*[height={height}][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
            f"bv*[height={height}][ext=mp4]+ba[ext=m4a]/"
            f"bv*[height={height}]+ba/"
            f"b[height={height}][protocol^=m3u8]/"
            f"b[height={height}]"
        )
        if prefer_hls:
            return f"b[height={height}][protocol^=m3u8]/{selector}"
        return selector
    return "bv*+ba/b"


def single_file_format_selector(options: DownloadOptions) -> str:
    if options.format_id:
        return options.format_id
    if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
        height = int(options.resolution[:-1])
        return f"b[height={height}][ext=mp4]/b[height={height}]"
    return "best[ext=mp4]/best"


def requires_ffmpeg(options: DownloadOptions) -> bool:
    return bool(options.format_id)


def resolution_from_info_dict(info: dict[str, Any]) -> tuple[int, int] | None:
    direct = resolution_from_mapping(info)
    if direct:
        return direct

    requested_formats = info.get("requested_formats")
    if not isinstance(requested_formats, list):
        return None

    candidates = [
        resolution
        for fmt in requested_formats
        if isinstance(fmt, dict)
        and fmt.get("vcodec") not in {None, "none"}
        and (resolution := resolution_from_mapping(fmt))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda resolution: resolution[0] * resolution[1])


def actual_format_from_info_dict(info: dict[str, Any]) -> str | None:
    requested_formats = info.get("requested_formats")
    if isinstance(requested_formats, list):
        video = next(
            (
                fmt
                for fmt in requested_formats
                if isinstance(fmt, dict) and fmt.get("vcodec") not in {None, "none"}
            ),
            None,
        )
        audio = next(
            (
                fmt
                for fmt in requested_formats
                if isinstance(fmt, dict) and fmt.get("acodec") not in {None, "none"}
            ),
            None,
        )
        if video:
            ext = str(video.get("ext") or info.get("ext") or "").strip()
            codecs = [short_codec(video.get("vcodec"))]
            if audio:
                codecs.append(short_codec(audio.get("acodec")))
            codec_label = " + ".join(codec for codec in codecs if codec)
            return " · ".join(part for part in [ext, codec_label] if part) or None

    ext = str(info.get("ext") or "").strip()
    codecs = [
        short_codec(info.get("vcodec")),
        short_codec(info.get("acodec")),
    ]
    codec_label = " + ".join(codec for codec in codecs if codec)
    return " · ".join(part for part in [ext, codec_label] if part) or None


def filesize_from_info_dict(info: dict[str, Any]) -> int | None:
    requested_formats = info.get("requested_formats")
    if isinstance(requested_formats, list):
        total = 0
        for fmt in requested_formats:
            if not isinstance(fmt, dict):
                continue
            size = positive_int(fmt.get("filesize")) or positive_int(fmt.get("filesize_approx"))
            if size is not None:
                total += size
        return total or None
    return positive_int(info.get("filesize")) or positive_int(info.get("filesize_approx"))


def resolution_from_mapping(value: dict[str, Any]) -> tuple[int, int] | None:
    width = positive_int(value.get("width"))
    height = positive_int(value.get("height"))
    if width is None or height is None:
        return None
    return width, height


def positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def short_codec(value: Any) -> str | None:
    if not value or value == "none":
        return None
    return str(value).split(".", 1)[0]


def resolution_height(resolution: str) -> int | None:
    if not resolution.endswith("p"):
        return None
    value = resolution[:-1]
    if not value.isdigit():
        return None
    return int(value)


def suggest_lower_resolution(
    requested_resolution: str,
    formats: list[FormatOption],
    min_height: int = DEFAULT_MIN_AUTO_FALLBACK_HEIGHT,
    allow_below_min_if_source_below_min: bool = False,
) -> str | None:
    requested_height = resolution_height(requested_resolution)
    if requested_height is None:
        return None
    heights = {
        int(format.height)
        for format in formats
        if format.height is not None
    }
    lower_heights = {
        height
        for height in heights
        if height < requested_height
    }
    safe_lower_heights = {
        height
        for height in lower_heights
        if height >= min_height
    }
    if safe_lower_heights:
        return f"{max(safe_lower_heights)}p"
    if allow_below_min_if_source_below_min and lower_heights and not any(height >= min_height for height in heights):
        return f"{max(lower_heights)}p"
    return None


def has_resolution_at_or_above(
    formats: list[FormatOption],
    min_height: int = DEFAULT_MIN_AUTO_FALLBACK_HEIGHT,
) -> bool:
    return any(
        int(format.height)
        for format in formats
        if format.height is not None and int(format.height) >= min_height
    )

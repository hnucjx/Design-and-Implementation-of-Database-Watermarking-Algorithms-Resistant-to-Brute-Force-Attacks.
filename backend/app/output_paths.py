from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse


SIDECAR_SUFFIXES = [".description", ".info.json", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".vtt"]
MERGED_OUTPUT_SUFFIXES = [".mp4", ".mkv", ".webm"]
MEDIA_SUFFIXES = [".mp4", ".mkv", ".webm", ".m4v", ".mov"]
PARTIAL_SUFFIXES = [".part", ".ytdl", ".tmp", ".temp"]
FORMAT_SUFFIX_PATTERN = re.compile(r"^(?P<stem>.+)\.f\d+(?P<suffix>\.[^.]+)$")


def resolve_existing_output_path(output_path: Path, base_dir: Path | None = None) -> Path | None:
    for path in _path_variants(output_path, base_dir):
        for candidate in merged_output_path_candidates(path):
            if candidate.is_file():
                return candidate
        if path.is_file():
            return path
    return None


def discover_existing_output_path(source_url: str, job_download_dir: Path | None) -> Path | None:
    for path in discover_output_file_candidates(source_url, job_download_dir):
        if path.suffix.lower() in MEDIA_SUFFIXES and not _is_partial_path(path):
            return path
    return None


def discover_output_file_candidates(source_url: str, job_download_dir: Path | None) -> list[Path]:
    if job_download_dir is None:
        return []
    directory = job_download_dir.expanduser()
    if not directory.is_dir():
        return []
    video_id = source_video_id(source_url)
    if not video_id:
        return []
    token = f"[{video_id}]"
    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file() and token in path.name
    ]
    return sorted(candidates, key=lambda path: (not _is_preferred_media_path(path), path.name.lower()))


def source_video_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = parsed.netloc.lower()
    if "youtu.be" in host:
        value = parsed.path.strip("/").split("/", 1)[0]
        return value or None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return query_id
    path_parts = [part for part in parsed.path.split("/") if part]
    for marker in ("shorts", "embed", "live"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if index + 1 < len(path_parts):
                return path_parts[index + 1]
    return None


def merged_output_path_candidates(output_path: Path) -> list[Path]:
    match = FORMAT_SUFFIX_PATTERN.match(output_path.name)
    if not match:
        return []

    base = output_path.with_name(match.group("stem"))
    original_suffix = match.group("suffix").lower()
    suffixes = [".mp4", original_suffix, *MERGED_OUTPUT_SUFFIXES]
    candidates: list[Path] = []
    seen: set[Path] = set()
    for suffix in suffixes:
        candidate = base.with_suffix(suffix)
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def output_file_candidates(output_path: Path, base_dir: Path | None = None) -> list[Path]:
    base_paths: list[Path] = []
    for path in _path_variants(output_path, base_dir):
        base_paths.extend([path, *merged_output_path_candidates(path)])
    candidates: list[Path] = []
    seen: set[Path] = set()
    for base_path in base_paths:
        for candidate in [base_path, *(base_path.with_suffix(suffix) for suffix in SIDECAR_SUFFIXES)]:
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates


def _is_preferred_media_path(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_SUFFIXES and not _is_partial_path(path)


def _is_partial_path(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in PARTIAL_SUFFIXES)


def _path_variants(output_path: Path, base_dir: Path | None) -> list[Path]:
    path = output_path.expanduser()
    variants: list[Path] = []
    if base_dir is not None and not path.is_absolute():
        variants.append(base_dir.expanduser() / path)
    variants.append(path)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            deduped.append(variant)
    return deduped

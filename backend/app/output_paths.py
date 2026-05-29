from pathlib import Path
import re


SIDECAR_SUFFIXES = [".description", ".info.json", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".vtt"]
MERGED_OUTPUT_SUFFIXES = [".mp4", ".mkv", ".webm"]
FORMAT_SUFFIX_PATTERN = re.compile(r"^(?P<stem>.+)\.f\d+(?P<suffix>\.[^.]+)$")


def resolve_existing_output_path(output_path: Path, base_dir: Path | None = None) -> Path | None:
    for path in _path_variants(output_path, base_dir):
        if path.is_file():
            return path
        for candidate in merged_output_path_candidates(path):
            if candidate.is_file():
                return candidate
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

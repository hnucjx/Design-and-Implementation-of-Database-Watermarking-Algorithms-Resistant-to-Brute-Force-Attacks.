"""产物位置的计算：候选链、成品定位，以及**与重构前行为的逐项对照**。

本文件的后半部分是「行为不变性取证」（总纲 §3 第 6 条）：把重构前的三处实现内联成 `_legacy_*`，
对同一批磁盘场景断言新旧入口给出**完全相同**的结果。`ai/refactor/004` 声称「候选链从两条收敛
成一条且行为不变」，靠的就是这里。
"""

from pathlib import Path

import pytest

from app.output_paths import (
    discover_existing_output_path,
    discover_output_file_candidates,
    existing_item_folder,
    existing_output_file,
    item_artifact_candidates,
    resolve_existing_output_path,
)


URL = "https://www.youtube.com/watch?v=abc123"


def _seed(directory: Path, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("x", encoding="utf-8")


def _record_path(directory: Path, name: str | None, absolute: bool) -> str | None:
    if name is None:
        return None
    return str(directory / name) if absolute else name


# --- 重构前的实现（逐字复刻，仅供对照；不要在产线代码里复活它们）------------------

def _legacy_output_file(output_path, source_url, base_dir):
    path = resolve_existing_output_path(Path(output_path), base_dir) if output_path else None
    if path is None:
        path = discover_existing_output_path(source_url, base_dir)
    return path


def _legacy_item_folder(output_path, source_url, base_dir):
    path = resolve_existing_output_path(Path(output_path), base_dir) if output_path else None
    if not path:
        discovered = discover_output_file_candidates(source_url, base_dir)
        path = discovered[0] if discovered else None
    if path:
        return path.parent
    return None


def _legacy_item_output_paths(output_path, source_url, base_dir):
    paths = []
    if output_path:
        paths.append(Path(output_path))
    paths.extend(discover_output_file_candidates(source_url, base_dir))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in paths:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


# --- 对照场景表 ---------------------------------------------------------------

SCENARIOS = [
    pytest.param([], None, False, id="empty-dir-no-record"),
    pytest.param([], "作品 [abc123].mp4", False, id="record-missing-no-files"),
    pytest.param(["作品 [abc123].mp4"], "作品 [abc123].mp4", False, id="record-points-to-existing"),
    pytest.param(["作品 [abc123].mp4"], "作品 [abc123].f137.mp4", False, id="record-is-fragment-merge-exists"),
    pytest.param(["作品 [abc123].mp4"], None, False, id="no-record-discovered-media"),
    pytest.param(["作品 [abc123].webm", "作品 [abc123].vtt"], None, False, id="media-and-sidecar"),
    pytest.param(["作品 [abc123].vtt"], None, False, id="sidecar-only-is-not-a-product"),
    pytest.param(["作品 [abc123].part"], None, False, id="partial-only-is-not-a-product"),
    pytest.param(["别的 [zzz999].mp4"], "作品 [abc123].mp4", False, id="other-video-id-ignored"),
    pytest.param([], None, True, id="absolute-none"),
    pytest.param(["作品 [abc123].mp4"], "作品 [abc123].mp4", True, id="absolute-record-existing"),
    pytest.param(["作品 [abc123].mkv"], "作品 [abc123].mp4", True, id="absolute-record-missing"),
]


@pytest.mark.parametrize("files, output_name, absolute", SCENARIOS)
def test_single_candidate_chain_matches_pre_refactor_logic(
    tmp_path: Path, files: list[str], output_name: str | None, absolute: bool
) -> None:
    """收敛后的三个入口与重构前的三处实现，在同一批磁盘场景上逐项一致。"""
    downloads = tmp_path / "downloads"
    _seed(downloads, files)
    output_path = _record_path(downloads, output_name, absolute)

    assert item_artifact_candidates(output_path, URL, downloads) == _legacy_item_output_paths(
        output_path, URL, downloads
    )
    assert existing_output_file(output_path, URL, downloads) == _legacy_output_file(output_path, URL, downloads)
    assert existing_item_folder(output_path, URL, downloads) == _legacy_item_folder(output_path, URL, downloads)


# --- 直接语义断言（钉住「为什么是这个结果」）--------------------------------------

def test_candidate_chain_puts_recorded_path_first(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    _seed(downloads, ["作品 [abc123].mp4"])
    candidates = item_artifact_candidates("作品 [abc123].mkv", URL, downloads)
    assert candidates[0] == Path("作品 [abc123].mkv")
    assert downloads / "作品 [abc123].mp4" in candidates


def test_candidate_chain_dedupes_when_record_matches_discovery(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    _seed(downloads, ["作品 [abc123].mp4"])
    recorded = str(downloads / "作品 [abc123].mp4")
    # 记录路径与磁盘发现指向同一个文件：只能出现一次。
    assert item_artifact_candidates(recorded, URL, downloads) == [Path(recorded)]


def test_candidate_chain_returns_empty_without_record_or_discovery(tmp_path: Path) -> None:
    assert item_artifact_candidates(None, URL, tmp_path / "empty") == []


def test_existing_output_file_never_returns_a_sidecar(tmp_path: Path) -> None:
    """字幕是 sidecar，不是成品 —— 不能因为「它存在」就把它当成可播放的视频。"""
    downloads = tmp_path / "downloads"
    _seed(downloads, ["作品 [abc123].srt"])
    assert existing_output_file(None, URL, downloads) is None


def test_existing_output_file_returns_none_when_everything_is_missing(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    _seed(downloads, ["别的 [zzz999].mp4"])
    assert existing_output_file("作品 [abc123].mp4", URL, downloads) is None


def test_existing_item_folder_falls_back_to_discovered_parent(tmp_path: Path) -> None:
    """产物还没登记，但磁盘上已经能发现候选：退回**它的父目录**，而不是下载根。"""
    downloads = tmp_path / "downloads"
    _seed(downloads, ["作品 [abc123].webm"])
    assert existing_item_folder(None, URL, downloads) == downloads

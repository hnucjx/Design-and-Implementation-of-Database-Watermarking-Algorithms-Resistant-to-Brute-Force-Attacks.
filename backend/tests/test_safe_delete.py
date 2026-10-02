"""删除白名单的路径安全判定。

删除是唯一会破坏用户数据的动作，而它的核心判据（「这个路径在不在允许范围内」）过去只能跑
任务来验。本文件把它当纯函数验：全部用 `tmp_path`，不起服务、不联网、不写库。

**重点覆盖的是「字符串前缀」型误判** —— 白名单里最经典的漏洞是
`str(path).startswith(str(root))`：它会把 `.../downloads-other` 当成 `.../downloads` 的子路径。
这里的判据是路径段比较，本文件负责证明它确实不是靠前缀。
"""

import os
from pathlib import Path

import pytest

from app.safe_delete import DeleteScope, delete_scope, is_deletion_allowed, removable_job_dir


# --- delete_scope ------------------------------------------------------------

def test_scope_without_job_dir_only_allows_the_download_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    scope = delete_scope(root, None)
    assert scope.download_root == root.resolve()
    assert scope.job_root is None
    assert scope.allowed_roots == (root.resolve(),)


def test_scope_adds_the_job_dir_when_it_lives_below_the_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    job = root / "playlist-abc"
    scope = delete_scope(root, job)
    assert scope.job_root == job.resolve()
    assert scope.allowed_roots == (root.resolve(), job.resolve())


def test_scope_dedupes_when_job_dir_is_the_download_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    scope = delete_scope(root, root)
    assert scope.allowed_roots == (root.resolve(),)


def test_scope_keeps_a_job_dir_outside_the_root(tmp_path: Path) -> None:
    """任务目录可能在下载根之外（用户改过设置）—— 那也要允许，否则那个任务的产物删不掉。"""
    root = tmp_path / "downloads"
    elsewhere = tmp_path / "elsewhere"
    scope = delete_scope(root, elsewhere)
    assert elsewhere.resolve() in scope.allowed_roots


def test_scope_normalizes_relative_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    scope = delete_scope(Path("downloads"), Path("downloads") / "job-a")
    assert scope.download_root.is_absolute()
    assert scope.job_root is not None and scope.job_root.is_absolute()


# --- is_deletion_allowed -----------------------------------------------------

def test_is_deletion_allowed_accepts_root_and_descendants(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    scope = DeleteScope(download_root=root, job_root=None, allowed_roots=(root,))
    assert is_deletion_allowed(root, scope)
    assert is_deletion_allowed(root / "job" / "video.mp4", scope)


def test_is_deletion_allowed_rejects_parent_of_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    scope = DeleteScope(download_root=root, job_root=None, allowed_roots=(root,))
    assert not is_deletion_allowed(tmp_path, scope)


def test_is_deletion_allowed_rejects_sibling_that_shares_a_string_prefix(tmp_path: Path) -> None:
    """`downloads-other` 与 `downloads` 共享字符串前缀，但不是它的子路径 —— 必须拒绝。"""
    root = tmp_path / "downloads"
    sibling = tmp_path / "downloads-other" / "video.mp4"
    scope = DeleteScope(download_root=root, job_root=None, allowed_roots=(root,))
    assert str(sibling).startswith(str(root))  # 前提：字符串前缀确实骗得过朴素实现
    assert not is_deletion_allowed(sibling, scope)


def test_is_deletion_allowed_accepts_the_second_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    elsewhere = tmp_path / "elsewhere"
    scope = DeleteScope(download_root=root, job_root=elsewhere, allowed_roots=(root, elsewhere))
    assert is_deletion_allowed(elsewhere / "video.mp4", scope)
    assert not is_deletion_allowed(tmp_path / "third" / "video.mp4", scope)


def test_is_deletion_allowed_rejects_unrelated_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    scope = DeleteScope(download_root=root, job_root=None, allowed_roots=(root,))
    assert not is_deletion_allowed(Path("C:/Windows/System32/drivers/etc/hosts"), scope)


@pytest.mark.skipif(os.name != "nt", reason="大小写不敏感是 Windows 文件系统语义")
def test_is_deletion_allowed_treats_case_differences_as_the_same_root(tmp_path: Path) -> None:
    """Windows 上 `Downloads` 与 `downloads` 是同一个目录。

    这条同时挡住一种退化：若判据被改成字符串前缀比较，大小写不同的同一个根就会被**误拒**
    （用户手输的路径大小写与配置不一致时，产物就删不掉了）。
    """
    root = tmp_path / "downloads"
    scope = DeleteScope(download_root=root, job_root=None, allowed_roots=(root,))
    assert is_deletion_allowed(tmp_path / "DOWNLOADS" / "video.mp4", scope)


# --- removable_job_dir -------------------------------------------------------

def test_removable_job_dir_returns_a_true_subdirectory(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    job = root / "playlist-abc"
    assert removable_job_dir(delete_scope(root, job)) == job.resolve()


def test_removable_job_dir_is_none_for_the_root_itself(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    assert removable_job_dir(delete_scope(root, root)) is None


def test_removable_job_dir_is_none_for_a_parent_of_the_root(tmp_path: Path) -> None:
    """下载根是 `.../downloads/inner`，任务目录是 `.../downloads` —— 收走它等于删掉根的一部分。"""
    root = tmp_path / "downloads" / "inner"
    outer = tmp_path / "downloads"
    assert removable_job_dir(delete_scope(root, outer)) is None


def test_removable_job_dir_rejects_sibling_with_shared_string_prefix(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    sibling = tmp_path / "downloads-other"
    assert removable_job_dir(delete_scope(root, sibling)) is None


def test_removable_job_dir_is_none_without_a_job_dir(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    assert removable_job_dir(delete_scope(root, None)) is None

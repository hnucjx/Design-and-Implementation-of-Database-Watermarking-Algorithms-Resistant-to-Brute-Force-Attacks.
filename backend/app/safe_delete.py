"""删除产物时的**路径安全判定**（纯逻辑）。

删除是整个应用里唯一会**破坏用户数据**的动作，而它的承重部分其实只有一个问题：
*这个路径在不在允许删除的范围内？* 这段判定过去只能靠「跑一次任务，看文件有没有被删」来验
—— 于是它既不敢改，也说不清算错在哪。本模块把它收成纯函数：输入路径，输出布尔或路径，
**不删任何东西**。真正 `unlink()` / `rmdir()` 由 [`job_manager`](job_manager.py) 执行。

允许范围 = 下载根 ∪ 本任务自己的下载目录。任务目录可能不在下载根之下（用户改过下载设置），
所以两个根都要带上；同根的情况会去重，避免同一条路径被判定两次。

不负责：枚举要删的候选（那是 [`output_paths`](output_paths.py) 的 `item_artifact_candidates` /
`output_file_candidates`），不负责真正的删除（那是 `job_manager`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeleteScope:
    """一次删除的**允许范围**。

    `job_root` 为 None 表示「任务目录不可用」或「没提供」，两者在下游判定里等价。
    `allowed_roots` 已去重，且**保序**（下载根在前）—— 顺序不影响布尔结果，但可读性依赖它。
    """

    download_root: Path
    job_root: Path | None
    allowed_roots: tuple[Path, ...]


def delete_scope(download_dir: Path, job_download_dir: Path | None) -> DeleteScope:
    """从「下载根」与「任务目录」算出本次删除的允许范围。

    两个根的失败处理**刻意不同**：下载根来自配置，解析不了说明配置本身有问题，应当让它抛
    （启动时就炸出来，好过等到用户点「删除」才发现删不动）；任务目录解析不了只说明这个任务
    目录不可用，跳过即可 —— 此时允许范围退化成「只有下载根」。
    """
    download_root = download_dir.expanduser().resolve()
    job_root = _resolve_quietly(job_download_dir)
    allowed: tuple[Path, ...]
    if job_root is None or job_root == download_root:
        allowed = (download_root,)
    else:
        allowed = (download_root, job_root)
    return DeleteScope(download_root=download_root, job_root=job_root, allowed_roots=allowed)


def is_deletion_allowed(path: Path, scope: DeleteScope) -> bool:
    """路径是否落在允许范围内（含根本身）。

    判据是**路径段**比较（`root in path.parents`），不是字符串前缀 —— 后者会把
    `/downloads-other` 误判成 `/downloads` 的子路径，正是这类「白名单形同虚设」的经典成因。

    传入前请先 `resolve()`：只有解析后的最终位置参与判定，因此指向根之外的符号链接会被拒绝。
    """
    return any(path == root or root in path.parents for root in scope.allowed_roots)


def removable_job_dir(scope: DeleteScope) -> Path | None:
    """任务目录里**可以被收走**的那个路径：必须是下载根的**真**子目录，否则 None。

    收走的是「这个任务自己的空目录」（下载完清理）。等于下载根、或是下载根的父级都不算 ——
    删掉用户选定的下载根本身、或删掉它的上级目录，都不是「清理这个任务的产物」该做的事。
    """
    if scope.job_root is None or scope.job_root == scope.download_root:
        return None
    if scope.download_root not in scope.job_root.parents:
        return None
    return scope.job_root


def _resolve_quietly(path: Path | None) -> Path | None:
    """`resolve()`，失败返回 None（不抛）。`Path.resolve()` 非严格模式下基本不抛，这里是兜底。"""
    if path is None:
        return None
    try:
        return path.expanduser().resolve()
    except OSError:
        return None

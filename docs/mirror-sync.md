# 仓库自动镜像同步方案

## 概述

本仓库（`0xaiio/cascade`）与仓库 `hnucjx/Design-and-Implementation-of-Database-Watermarking-Algorithms-Resistant-to-Brute-Force-Attacks.` 内容保持一致。通过本地脚本 `scripts/mirror.sh` 执行单向严格镜像同步。

## 同步策略

- **同步方向**：`0xaiio/cascade` → `hnucjx/...`（单向）
- **同步范围**：全部分支、全部标签、完整提交历史
- **冲突处理**：使用 `--mirror --force` 强制覆盖目标仓库
- **目标仓库角色**：严格只读镜像，不直接在目标仓库提交

## 运行方式

### 1. 手动运行

在仓库根目录下执行：

```bash
TARGET_PAT=ghp_xxx ./scripts/mirror.sh
```

其中 `ghp_xxx` 替换为对目标仓库有写权限的 GitHub Personal Access Token。

### 2. Windows 任务计划程序定时运行

1. 打开 Windows 任务计划程序。
2. 创建基本任务，设置触发器（例如每天一次）。
3. 设置操作：启动程序。
   - 程序：`C:\Program Files\Git\bin\bash.exe`
   - 参数：`D:\code-repo\cascade\scripts\mirror.sh`
   - 起始于：`D:\code-repo\cascade`
4. 在操作系统的环境变量中添加 `TARGET_PAT`，或在任务属性中设置环境变量。

### 3. Linux / macOS 定时运行

使用 `crontab` 定时执行，例如每 10 分钟同步一次：

```bash
*/10 * * * * export TARGET_PAT=ghp_xxx; /bin/bash /path/to/cascade/scripts/mirror.sh >> /path/to/cascade/logs/mirror.log 2>&1
```

## 脚本说明

文件位置：`scripts/mirror.sh`

脚本逻辑：

1. 检查环境变量 `TARGET_PAT` 是否存在。
2. 在本地 `~/.cache/cascade-mirror` 维护 `cascade` 仓库的 `--mirror` 克隆。
3. 每次运行时先 `git fetch --all` 拉取最新内容。
4. 将目标仓库添加为 remote `mirror`。
5. 执行 `git push --mirror --force mirror`。

## 认证方式

使用 GitHub Personal Access Token（PAT）：

1. 在拥有目标仓库写权限的账号下生成 PAT。
2. 至少需要 `repo` 权限（或使用 Fine-grained PAT 授予目标仓库 `Contents: write` 权限）。
3. 通过环境变量 `TARGET_PAT` 传入脚本。

**注意**：PAT 不要写入脚本文件，避免泄露。

## 注意事项与风险

- `--mirror --force` 会覆盖目标仓库的全部内容，包括分支、标签和提交历史。
- 目标仓库独有的分支或标签会在同步后被删除。
- 所有代码修改请在 `cascade` 仓库进行，目标仓库仅作为只读镜像使用。
- 请勿在目标仓库配置反向同步，否则可能产生冲突。

## 历史记录

- 创建：配置基于 GitHub Actions + PAT 的单向严格镜像同步（后弃用，改为本地脚本方案）。
- 更新：改为本地 `scripts/mirror.sh` + 定时任务方案。

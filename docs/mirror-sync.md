# 仓库自动镜像同步方案

## 概述

本仓库（`0xaiio/cascade`）与仓库 `hnucjx/Design-and-Implementation-of-Database-Watermarking-Algorithms-Resistant-to-Brute-Force-Attacks` 内容保持一致。每当 `cascade` 仓库发生 `push` 操作时，GitHub Actions 会自动将最新内容以严格镜像方式同步到目标仓库。

## 同步策略

- **同步方向**：`0xaiio/cascade` → `hnucjx/...`（单向）
- **同步范围**：全部分支、全部标签、完整提交历史
- **冲突处理**：使用 `--mirror --force` 强制覆盖目标仓库
- **目标仓库角色**：严格只读镜像，不直接在目标仓库提交

## 触发条件

同步工作流 `.github/workflows/mirror.yml` 在以下情况触发：

- 任意分支发生 `push`
- 任意标签发生 `push`
- 手动触发 `workflow_dispatch`

## 认证方式

使用 GitHub Personal Access Token（PAT）：

1. 在拥有目标仓库写权限的账号下生成 PAT。
2. 至少需要 `repo` 权限（或使用 Fine-grained PAT 授予目标仓库 `Contents: write` 权限）。
3. 将 PAT 作为名为 `TARGET_PAT` 的 Repository secret 添加到本仓库：
   - 路径：`Settings → Secrets and variables → Actions → New repository secret`
   - Name：`TARGET_PAT`
   - Value：生成的 PAT

## 工作流文件

文件位置：`.github/workflows/mirror.yml`

```yaml
name: Mirror to hnucjx repo

on:
  push:
    branches:
      - '**'
    tags:
      - '**'
  workflow_dispatch:

jobs:
  mirror:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout cascade with full history
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          fetch-tags: true

      - name: Configure Git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Push mirror to target repository
        env:
          TARGET_PAT: ${{ secrets.TARGET_PAT }}
        run: |
          git remote add mirror "https://${TARGET_PAT}@github.com/hnucjx/Design-and-Implementation-of-Database-Watermarking-Algorithms-Resistant-to-Brute-Force-Attacks.git"
          git push --mirror --force mirror
```

## 注意事项与风险

- `--mirror --force` 会覆盖目标仓库的全部内容，包括分支、标签和提交历史。
- 目标仓库独有的分支或标签会在同步后被删除。
- 所有代码修改请在 `cascade` 仓库进行，目标仓库仅作为只读镜像使用。
- 请勿在目标仓库配置反向同步，否则可能产生冲突。
- 如果同步失败，GitHub Actions 会自动发送邮件通知仓库管理员。

## 历史记录

- 创建：配置基于 GitHub Actions + PAT 的单向严格镜像同步。

#!/bin/bash
# 本地镜像同步脚本
# 用法：TARGET_PAT=ghp_xxx ./scripts/mirror.sh

set -e

PAT="${TARGET_PAT:-}"
if [ -z "$PAT" ]; then
  echo "Error: TARGET_PAT environment variable is not set." >&2
  exit 1
fi

SRC_URL="https://github.com/0xaiio/cascade.git"
TARGET_URL="https://${PAT}@github.com/hnucjx/Design-and-Implementation-of-Database-Watermarking-Algorithms-Resistant-to-Brute-Force-Attacks..git"
CACHE_DIR="${HOME}/.cache/cascade-mirror"

echo "Source: ${SRC_URL}"
echo "Target: https://***@github.com/hnucjx/..."

if [ ! -d "${CACHE_DIR}" ]; then
  echo "Initializing mirror cache at ${CACHE_DIR}..."
  mkdir -p "$(dirname "${CACHE_DIR}")"
  git clone --mirror "${SRC_URL}" "${CACHE_DIR}"
else
  echo "Fetching latest changes from source..."
  cd "${CACHE_DIR}"
  git fetch --all
fi

cd "${CACHE_DIR}"

# 配置目标仓库 remote（若已存在则更新 URL）
if git remote get-url mirror >/dev/null 2>&1; then
  git remote set-url mirror "${TARGET_URL}"
else
  git remote add mirror "${TARGET_URL}"
fi

echo "Pushing mirror to target repository..."
git push --mirror --force mirror

echo "Mirror sync completed."

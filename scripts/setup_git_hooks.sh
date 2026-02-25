#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

chmod +x .githooks/pre-push
git config core.hooksPath .githooks

echo "[hooks] 已启用版本化 Git hooks: core.hooksPath=.githooks"
echo "[hooks] 当前 pre-push 会阻止推送 venv/ 和 .venv/ 目录"

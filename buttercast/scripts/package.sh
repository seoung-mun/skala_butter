#!/bin/sh
# Submission zip: committed files only, without documents (see .gitattributes) or local state (.gitignore).
set -eu
cd "$(dirname "$0")/.."
if [ -n "$(git status --porcelain -- .)" ]; then
  echo "커밋되지 않은 변경이 있습니다. 커밋한 뒤 다시 실행하세요." >&2
  git status --short -- . >&2
  exit 1
fi
mkdir -p dist
# Only this project's folder, even when the git repository root is a parent folder.
# Run from the repository root: from a subfolder, git archive narrows the tree to that subfolder a second time.
git -C "$(git rev-parse --show-toplevel)" archive --format=zip --prefix=buttercast/ -o "$PWD/dist/buttercast.zip" "HEAD:$(git rev-parse --show-prefix)"
echo "dist/buttercast.zip"
unzip -l dist/buttercast.zip | tail -1

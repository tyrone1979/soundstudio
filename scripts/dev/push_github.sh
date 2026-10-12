#!/usr/bin/env bash
# Publish main to GitHub (tyrone1979/soundstudio) without the papers.
#
# Gitee (origin) carries the full repository and history. GitHub gets a
# separate public history: each run takes the tree of local HEAD, drops the
# excluded paths and commits it on top of GitHub's main. Paper history is
# therefore never pushed there.
#
#   bash scripts/dev/push_github.sh ["commit message"]
set -euo pipefail

cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
REMOTE="${GITHUB_REMOTE:-github}"
MSG="${1:-Sync from $(git rev-parse --short HEAD)}"

EXCLUDE=(
  papers
  runs/induction/paper
  score_learning/induction/paper.py
  tests/test_induction_paper.py
  "docs/论文同行评审_2026-10-06.md"
  "docs/论文同行评审_第二轮_2026-10-06.md"
  "docs/实验重设计与消融_2026-10-04.md"
  "docs/人工标注边界分析_2026-10-04.md"
  "docs/手工先验普查_2026-10-06.md"
)

INDEX="$(mktemp)"
trap 'rm -f "$INDEX"' EXIT
GIT_INDEX_FILE="$INDEX" git read-tree HEAD
GIT_INDEX_FILE="$INDEX" git rm -r -q --cached --ignore-unmatch -- "${EXCLUDE[@]}"
TREE="$(GIT_INDEX_FILE="$INDEX" git write-tree)"

for f in "${EXCLUDE[@]}"; do
  if git ls-tree -r --name-only "$TREE" -- "$f" | grep -q .; then
    echo "excluded path still present: $f" >&2
    exit 1
  fi
done

# RESET=1 starts a fresh one-commit history (force push), e.g. after
# extending EXCLUDE so that earlier GitHub commits no longer carry the files.
if [ "${RESET:-0}" = 1 ]; then
  COMMIT="$(git commit-tree "$TREE" -m "$MSG")"
  git push --force "$REMOTE" "${COMMIT}:refs/heads/main"
  exit 0
fi

git fetch -q "$REMOTE" main
PARENT="$(git rev-parse FETCH_HEAD)"
if [ "$(git rev-parse "$PARENT^{tree}")" = "$TREE" ]; then
  echo "GitHub is already up to date"
  exit 0
fi

COMMIT="$(git commit-tree "$TREE" -p "$PARENT" -m "$MSG")"
git push "$REMOTE" "${COMMIT}:refs/heads/main"

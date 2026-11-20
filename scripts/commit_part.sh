#!/usr/bin/env bash
# Commit one part's own files to that part's branch and push it, without
# switching the shared working tree's branch or touching its index.
# Every build agent shares one working tree, so nobody runs `git checkout`.
#
# Usage: scripts/commit_part.sh <branch> "<message>" <path> [<path>...]
# Example: scripts/commit_part.sh part/1-memory "Add Cognee recall" \
#            backend/app/sponsors/memory backend/tests/test_memory.py backend/requirements/memory.txt
set -euo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: $0 <branch> \"<message>\" <path> [<path>...]" >&2
  exit 2
fi

branch="$1"; msg="$2"; shift 2
cd "$(git rev-parse --show-toplevel)"
gitdir="$(git rev-parse --absolute-git-dir)"
export GIT_INDEX_FILE="$gitdir/index-${branch//\//_}"

if git rev-parse -q --verify "refs/heads/$branch" >/dev/null; then
  base="refs/heads/$branch"
else
  base="refs/heads/main"
fi

paths=()
for p in "$@"; do
  if [ -e "$p" ] || git ls-files --error-unmatch -- "$p" >/dev/null 2>&1; then
    paths+=("$p")
  fi
done
if [ "${#paths[@]}" -eq 0 ]; then
  echo "None of the given paths exist yet; nothing to commit." >&2
  exit 0
fi

git read-tree "$base"
git add -A -- "${paths[@]}"
tree="$(git write-tree)"
parent="$(git rev-parse "$base")"
if [ "$(git rev-parse "$parent^{tree}")" = "$tree" ]; then
  echo "No changes to commit on $branch."
  exit 0
fi

commit="$(printf '%s\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n' "$msg" \
  | git commit-tree "$tree" -p "$parent")"
git update-ref "refs/heads/$branch" "$commit"
git push -q origin "refs/heads/$branch:refs/heads/$branch"
echo "Committed $(git rev-parse --short "$commit") to $branch and pushed."

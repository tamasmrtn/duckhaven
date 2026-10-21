#!/usr/bin/env bash
# Check that every commit in <base>..<head> is signed off by its author under
# the Developer Certificate of Origin (https://developercertificate.org/).
# Used by the DCO job in CI on pull requests; bot commits (Dependabot) are exempt.
#
#   scripts/check-dco.sh origin/main HEAD
set -euo pipefail

base=$1
head=$2
missing=0

for sha in $(git rev-list --no-merges "$base..$head"); do
  email=$(git log -1 --format=%ae "$sha")
  case "$email" in
    *"[bot]@users.noreply.github.com") continue ;;
  esac
  if ! git log -1 --format=%B "$sha" | git interpret-trailers --parse \
      | grep -i '^Signed-off-by: ' | grep -qiF "<$email>"; then
    echo "::error::$(git log -1 --format='%h %s' "$sha") has no Signed-off-by for its author <$email>"
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  cat >&2 <<'MSG'
Every commit needs a Signed-off-by line matching its author. Sign off the
commits on your branch and push them again:

  git rebase --signoff origin/main
  git push --force-with-lease
MSG
  exit 1
fi
echo "Every commit is signed off."

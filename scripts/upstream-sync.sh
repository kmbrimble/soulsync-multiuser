#!/usr/bin/env bash
# Scheduled upstream sync for a Gitea-only fork. Run from a checkout of the fork's default branch.
#   UPSTREAM_URL     e.g. https://github.com/elad-bar/ha-blueiris.git   (public, anonymous fetch)
#   UPSTREAM_BRANCH  the upstream's default branch
#   BASE_BRANCH      the fork's default branch (the PR target)
#   GITEA_API        https://git.kiztigs.com/api/v1/repos/<owner>/<repo>   (only needed to open the PR)
#   GITEA_TOKEN      token that may push a branch and open a PR (optional with DRY_RUN=1)
#   UPSTREAM_RELEASE  set to 1 to track the upstream's latest published GitHub release (not drafts or pre-releases) instead of the tip of
#                     UPSTREAM_BRANCH. Needs UPSTREAM_URL to be https://github.com/<owner>/<repo>.git. The release lookup failing fails the job
#                     (it never falls back to the branch tip); no release at all fails it too.
#   UPSTREAM_RELEASE_API  override for the release lookup URL (default https://api.github.com/repos/<owner>/<repo>/releases/latest; tests use file://)
# Behaviour: up to date -> exit 0 quietly. Behind and the merge is clean -> push branch upstream-sync/<short sha> and open (or reuse) a PR
# "Sync upstream <sha>"; the repo's normal CI and branch protection then apply. Merge conflict -> no PR, an issue "Upstream sync needs a human"
# instead (once per upstream sha). Never force-pushes, never touches the base branch itself.
set -euo pipefail
: "${UPSTREAM_URL:?}" "${UPSTREAM_BRANCH:?}" "${BASE_BRANCH:?}"
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "$UPSTREAM_URL"
if [ "${UPSTREAM_RELEASE:-}" = 1 ]; then
  slug=${UPSTREAM_URL#https://github.com/}; slug=${slug%.git}
  api=${UPSTREAM_RELEASE_API:-https://api.github.com/repos/$slug/releases/latest}
  TAG=$(curl -fsS -m 30 -H 'Accept: application/vnd.github+json' "$api" | jq -er '.tag_name')
  # The tag ends up in a refspec and a branch name: accept only plain tag characters.
  [[ $TAG =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "refusing unexpected release tag: $TAG" >&2; exit 1; }
  git fetch -q upstream "refs/tags/$TAG"
  UP=$(git rev-parse "FETCH_HEAD^{commit}"); SHORT=$TAG; WHAT="release $TAG"
else
  git fetch -q upstream "$UPSTREAM_BRANCH"
  UP=$(git rev-parse FETCH_HEAD); SHORT=${UP:0:8}; WHAT=$UPSTREAM_BRANCH
fi
if git merge-base --is-ancestor "$UP" "origin/$BASE_BRANCH" 2>/dev/null || git merge-base --is-ancestor "$UP" HEAD; then
  echo "up to date with upstream ($SHORT)"; exit 0
fi
BRANCH="upstream-sync/$SHORT"
echo "upstream has new commits: $SHORT (behind by $(git rev-list --count HEAD.."$UP"))"

open_pr() {   # open the PR for $BRANCH unless one is already open
  local n
  n=$(curl -fsS -H "Authorization: token $GITEA_TOKEN" "$GITEA_API/pulls?state=open&limit=50" | jq --arg b "$BRANCH" '[.[]|select(.head.ref==$b)]|length')
  if [ "$n" = 0 ]; then
    jq -n --arg h "$BRANCH" --arg base "$BASE_BRANCH" --arg t "Sync upstream $SHORT" --arg b "Merges $UPSTREAM_URL ($WHAT @ $UP). Opened by the scheduled upstream-sync workflow; normal CI applies." '{head:$h,base:$base,title:$t,body:$b}' \
     | curl -fsS -X POST -H "Authorization: token $GITEA_TOKEN" -H "Content-Type: application/json" -d @- "$GITEA_API/pulls" | jq -r '"opened PR #\(.number)"'
  else echo "PR for $BRANCH already open"; fi
}
# Re-run for the same upstream commit: the branch already exists on the forge (a rebuilt merge commit would be a non-fast-forward push). Just make sure its PR exists.
if [ "${DRY_RUN:-}" != 1 ] && git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  echo "branch $BRANCH already pushed"; open_pr; exit 0
fi
git checkout -q -B "$BRANCH" "origin/$BASE_BRANCH" 2>/dev/null || git checkout -q -B "$BRANCH"
if ! git -c user.name="upstream-sync" -c user.email="upstream-sync@users.noreply.invalid" merge -q --no-edit "$UP" -m "Sync upstream $SHORT"; then
  git merge --abort || true
  echo "merge conflict with upstream $SHORT"
  if [ "${DRY_RUN:-}" != 1 ] && [ -n "${GITEA_API:-}" ]; then
    title="Upstream sync needs a human"
    # One open issue is enough however far upstream moves: reuse it (comment the new upstream sha once) instead of filing a new one per sha.
    existing=$(curl -fsS -H "Authorization: token $GITEA_TOKEN" "$GITEA_API/issues?state=open&type=issues&q=$(printf %s "$title" | jq -sRr @uri)&limit=50" | jq -r --arg t "$title" '[.[]|select(.title==$t)][0].number // empty')
    body="Merging $UPSTREAM_URL ($WHAT @ $UP) into $BASE_BRANCH conflicts. Resolve by hand (see CLAUDE.md, Syncing upstream)."
    if [ -z "$existing" ]; then
      jq -n --arg t "$title" --arg b "$body" '{title:$t,body:$b}' | curl -fsS -X POST -H "Authorization: token $GITEA_TOKEN" -H "Content-Type: application/json" -d @- "$GITEA_API/issues" | jq -r '"opened issue #\(.number)"'
    elif ! curl -fsS -H "Authorization: token $GITEA_TOKEN" "$GITEA_API/issues/$existing/comments" | jq -e --arg s "$SHORT" 'any(.[]; .body|contains($s))' >/dev/null; then
      jq -n --arg b "Still conflicting; upstream is now at $SHORT. $body" '{body:$b}' | curl -fsS -X POST -H "Authorization: token $GITEA_TOKEN" -H "Content-Type: application/json" -d @- "$GITEA_API/issues/$existing/comments" >/dev/null && echo "commented on issue #$existing"
    else echo "issue #$existing already mentions $SHORT"; fi
  fi
  exit 0
fi
if [ "${DRY_RUN:-}" = 1 ]; then echo "DRY_RUN: would push $BRANCH and open a PR ($(git rev-list --count "origin/$BASE_BRANCH..$BRANCH" 2>/dev/null || echo '?') commits)"; exit 0; fi
git push -q origin "$BRANCH"
open_pr

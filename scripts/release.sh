#!/bin/sh

# Create a dated release commit, tag it, and publish both atomically.
set -eu

remote="${RELEASE_REMOTE:-origin}"
test_target="${RELEASE_TEST_TARGET:-test}"
today="$(date +%Y%m%d)"
release_date="$(date +%F)"

fail() {
    printf '%s\n' "release: $*" >&2
    exit 1
}

working_tree_is_clean() {
    test -z "$(git status --porcelain)"
}

if ! working_tree_is_clean; then
    fail "the working tree must be clean before creating a release"
fi

git remote get-url "$remote" >/dev/null 2>&1 || fail "remote '$remote' does not exist"
git fetch --tags "$remote"

make "$test_target"

if ! working_tree_is_clean; then
    fail "tests modified the working tree; commit or discard those changes before releasing"
fi

tag="$today"
suffix=1
while git rev-parse -q --verify "refs/tags/$tag" >/dev/null; do
    tag="$today-$suffix"
    suffix=$((suffix + 1))
done

previous_tag="$(git tag --merged HEAD --list '[0-9]*' --sort=-version:refname | head -n 1)"
if [ -n "$previous_tag" ]; then
    changes="$(git log --format='- %s (%h)' "$previous_tag..HEAD")"
else
    changes="$(git log --format='- %s (%h)')"
fi

if [ -z "$changes" ]; then
    changes='- No code changes since the previous release.'
fi

changelog="CHANGELOG.md"
entry="## [$tag] - $release_date

$changes
"

if [ -f "$changelog" ]; then
    temp_file="$(mktemp)"
    {
        IFS= read -r first_line || true
        printf '%s\n\n' "$first_line"
        printf '%s\n' "$entry"
        cat
    } < "$changelog" > "$temp_file"
    mv "$temp_file" "$changelog"
else
    {
        printf '%s\n\n' '# Changelog'
        printf '%s\n' 'All notable changes to this project are documented in this file.'
        printf '\n%s\n' "$entry"
    } > "$changelog"
fi

git add "$changelog"
git commit -m "chore: release $tag"
git tag -a "$tag" -m "Release $tag"

branch="$(git branch --show-current)"
[ -n "$branch" ] || fail "releases must be created from a branch, not a detached HEAD"

if ! git push --atomic "$remote" "HEAD:refs/heads/$branch" "refs/tags/$tag"; then
    fail "publication failed; the local release commit and tag were kept for inspection"
fi

printf 'Released %s and pushed it to %s.\n' "$tag" "$remote"

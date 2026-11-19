#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: '$2' lacks '$3'"; fail=1;; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/seed" 2>/dev/null
cp -R "$src/queue.py" "$src/vendor" "$tmp/seed/"
echo 1 > "$tmp/seed/f"
git -C "$tmp/seed" add -A; git -C "$tmp/seed" commit -qm one; git -C "$tmp/seed" push -q origin main
git clone -q "$tmp/origin.git" "$tmp/pkg"
ens() { "$tmp/pkg/queue.py" update; }
advance() { echo "$1" > "$tmp/seed/f"; git -C "$tmp/seed" commit -qam "$1"; git -C "$tmp/seed" push -q origin main; }
at() { git -C "$tmp/pkg" rev-parse HEAD; }

out=$(ens 2>&1); rc=$?
check "already current exits 0" "$rc" 0
check "already current says so" "$out" "zstack: already at $(at)"

old=$(at); advance two; new=$(git -C "$tmp/seed" rev-parse HEAD)
out=$(ens 2>&1); rc=$?
check "behind main fast-forwards and exits 0" "$rc" 0
check "fast-forward prints old -> new" "$out" "zstack: $old -> $new"
check "checkout is at the new main" "$(at)" "$new"

advance three; echo dirty > "$tmp/pkg/f"; before=$(at)
out=$(ens 2>&1 >/dev/null); rc=$?
check "dirty tree exits 1" "$rc" 1
has "dirty tree names the failure" "$out" "uncommitted changes"
check "dirty tree is not updated" "$(at)" "$before"
check "dirty tree keeps the local edit" "$(cat "$tmp/pkg/f")" dirty

git -C "$tmp/pkg" checkout -q -- f

echo local > "$tmp/pkg/g"; git -C "$tmp/pkg" add g; git -C "$tmp/pkg" commit -qm local; before=$(at)
out=$(ens 2>&1 >/dev/null); rc=$?
check "diverged main exits 1" "$rc" 1
has "diverged main names the failure" "$out" "not a fast-forward"
check "diverged main is not moved" "$(at)" "$before"

git -C "$tmp/pkg" checkout -q --detach
out=$(ens 2>&1 >/dev/null); rc=$?
check "detached HEAD exits 1" "$rc" 1
has "detached HEAD names the failure" "$out" "detached HEAD"

git -C "$tmp/pkg" checkout -q main; git -C "$tmp/pkg" reset -q --hard origin/main
git -C "$tmp/pkg" remote set-url origin "$tmp/missing.git"
out=$(ens 2>&1 >/dev/null); rc=$?
check "failed fetch exits 1" "$rc" 1
has "failed fetch names the failure" "$out" "fetch origin main failed"
exit $fail

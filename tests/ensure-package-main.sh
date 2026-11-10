#!/bin/bash
# Checks bin/ensure-package-main and bin/pi-run's use of it against throwaway local repos. No network.
# usage: tests/ensure-package-main.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: '$2' lacks '$3'"; fail=1;; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# a pi with claude-code logged in, so a dry launch gets past the credential check on any machine
mkdir "$tmp/bin"; printf '#!/bin/sh\necho "claude-code m"\n' > "$tmp/bin/pi"; chmod +x "$tmp/bin/pi"; export PATH="$tmp/bin:$PATH"
export ONIGIRI_HOST=   # set and empty: no host file, whatever ~/.config/onigiri/host says; the copies under test have no hosts/ dir
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# origin.git is the remote main; seed pushes to it; pkg is the installed checkout under test.
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/seed" 2>/dev/null
mkdir -p "$tmp/seed/bin" "$tmp/seed/config"
cp "$src/bin/ensure-package-main" "$src/bin/model-for" "$src/bin/pi-run" "$tmp/seed/bin/"
cp "$src/config/models.json" "$tmp/seed/config/"
echo 1 > "$tmp/seed/f"
git -C "$tmp/seed" add -A; git -C "$tmp/seed" commit -qm one; git -C "$tmp/seed" push -q origin main
git clone -q "$tmp/origin.git" "$tmp/pkg"
ens="$tmp/pkg/bin/ensure-package-main"
advance() { echo "$1" > "$tmp/seed/f"; git -C "$tmp/seed" commit -qam "$1"; git -C "$tmp/seed" push -q origin main; }
at() { git -C "$tmp/pkg" rev-parse HEAD; }

out=$("$ens" 2>&1); rc=$?
check "already current exits 0" "$rc" 0
check "already current says so" "$out" "zstack: already at $(at)"

old=$(at); advance two; new=$(git -C "$tmp/seed" rev-parse HEAD)
out=$("$ens" 2>&1); rc=$?
check "behind main fast-forwards and exits 0" "$rc" 0
check "fast-forward prints old -> new" "$out" "zstack: $old -> $new"
check "checkout is at the new main" "$(at)" "$new"

advance three; echo dirty > "$tmp/pkg/f"; before=$(at)
out=$("$ens" 2>&1 >/dev/null); rc=$?
check "dirty tree exits 1" "$rc" 1
has "dirty tree names the failure" "$out" "uncommitted changes"
check "dirty tree is not updated" "$(at)" "$before"
check "dirty tree keeps the local edit" "$(cat "$tmp/pkg/f")" dirty

# the launcher refuses to start (exit 4, no dry-run line) while ensure fails, even in dry-run mode.
out=$(PI_RUN_DRY=1 ONIGIRI_ROOT="$tmp/root" "$tmp/pkg/bin/pi-run" s coding /dev/null 2>&1); rc=$?
check "pi-run aborts the launch when ensure fails" "$rc" 4
has "pi-run passes the ensure error through" "$out" "uncommitted changes"
check "pi-run made no review dir" "$(ls "$tmp/root" 2>/dev/null)" ""
git -C "$tmp/pkg" checkout -q -- f

echo local > "$tmp/pkg/g"; git -C "$tmp/pkg" add g; git -C "$tmp/pkg" commit -qm local; before=$(at)
out=$("$ens" 2>&1 >/dev/null); rc=$?
check "diverged main exits 1" "$rc" 1
has "diverged main names the failure" "$out" "not a fast-forward"
check "diverged main is not moved" "$(at)" "$before"

git -C "$tmp/pkg" checkout -q --detach
out=$("$ens" 2>&1 >/dev/null); rc=$?
check "detached HEAD exits 1" "$rc" 1
has "detached HEAD names the failure" "$out" "detached HEAD"

git -C "$tmp/pkg" checkout -q main; git -C "$tmp/pkg" reset -q --hard origin/main
echo task > "$tmp/brief.md"
out=$(PI_RUN_DRY=1 ONIGIRI_ROOT="$tmp/root" "$tmp/pkg/bin/pi-run" s coding "$tmp/brief.md" 2>&1); rc=$?
check "pi-run dry run on a current tree exits 0" "$rc" 0
has "pi-run dry run reports the pin" "$out" "zstack: already at $(at)"
has "pi-run dry run gets past ensure" "$out" "dry run, would start s"
git -C "$tmp/pkg" remote set-url origin "$tmp/missing.git"
out=$("$ens" 2>&1 >/dev/null); rc=$?
check "failed fetch exits 1" "$rc" 1
has "failed fetch names the failure" "$out" "fetch origin main failed"
exit $fail

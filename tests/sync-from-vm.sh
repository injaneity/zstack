#!/bin/bash
# Checks hosts/mini/sync-from-vm.sh against a fake VM wrapper in a throwaway HOME. No network.
# usage: tests/sync-from-vm.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
tmp=$(cd "$(mktemp -d)" && pwd -P); trap 'chmod -R u+w "$tmp"; rm -rf "$tmp"' EXIT
mkdir -p "$tmp/vm"
printf 'hold | 1 | all | x\n' > "$tmp/vm/hold-prs.txt"
# the fake wrapper runs its one shell line locally, with /srv/onigiri standing for $tmp/vm. It reads the file first and
# answers after $DELAY seconds (0.2 by default), like a slow ssh link.
cat > "$tmp/fake-vm" <<'W'
#!/bin/sh
out=$(eval "$(echo "$1" | sed "s#/srv/onigiri#$HOME/vm#g")") || exit 1
sleep "${DELAY:-0.2}"; printf '%s\n' "$out"
W
chmod +x "$tmp/fake-vm"
sync() { HOME="$tmp" ONIGIRI_VM="$tmp/fake-vm" "$src/hosts/mini/sync-from-vm.sh"; }
d=$tmp/onigiri/sync

sync 2>"$tmp/err"; check "a sync exits 0" "$?" 0
check "it copies the VM's holds file" "$(cat "$d/hold-prs.txt")" "hold | 1 | all | x"
check "the copy is read-only" "$([ -w "$d/hold-prs.txt" ] && echo writable || echo read-only)" read-only
check "it writes synced-at" "$(grep -c SGT "$d/synced-at")" 1

printf 'hold | 2 | all | y\n' >> "$tmp/vm/hold-prs.txt"
pids=; for i in 1 2 3 4 5 6 7 8; do sync 2>"$tmp/err.$i" & pids="$pids $!"; done
rcs=; for p in $pids; do wait "$p"; rcs="$rcs$?"; done
check "eight launches at once all exit 0" "$rcs" 00000000
check "and print nothing on stderr" "$(cat "$tmp"/err.*)" ""
check "the copy is the VM's new file" "$(cat "$d/hold-prs.txt")" "$(cat "$tmp/vm/hold-prs.txt")"
check "no temp file is left behind" "$(cd "$d" && echo .[!.]* *)" ".lock hold-prs.txt synced-at"

# a slow launch reads the file, Zane edits it, a fast launch reads the edit: the slow one's older read must not win
DELAY=1.5 sync 2>"$tmp/err.slow" & slow=$!
sleep 0.5; printf 'hold | 3 | all | z\n' >> "$tmp/vm/hold-prs.txt"
DELAY=0 sync 2>"$tmp/err.fast"; wait "$slow"
check "a slower launch that read earlier leaves the newer copy" "$(cat "$d/hold-prs.txt")" "$(cat "$tmp/vm/hold-prs.txt")"

mv "$tmp/vm/hold-prs.txt" "$tmp/vm/gone"
sync 2>"$tmp/err"; check "an unreachable holds file exits 1" "$?" 1
check "and keeps the old copy" "$(cat "$d/hold-prs.txt")" "$(cat "$tmp/vm/gone")"
check "with no temp file left" "$(cd "$d" && echo .[!.]* *)" ".lock hold-prs.txt synced-at"
exit $fail

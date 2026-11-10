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
# the fake wrapper runs its one shell line locally, with /srv/onigiri standing for $tmp/vm, and takes a moment like ssh
cat > "$tmp/fake-vm" <<'W'
#!/bin/sh
sleep 0.2; eval "$(echo "$1" | sed "s#/srv/onigiri#$HOME/vm#g")"
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
check "no temp file is left behind" "$(cd "$d" && echo .[!.]* *)" ".[!.]* hold-prs.txt synced-at"

mv "$tmp/vm/hold-prs.txt" "$tmp/vm/gone"
sync 2>"$tmp/err"; check "an unreachable holds file exits 1" "$?" 1
check "and keeps the old copy" "$(cat "$d/hold-prs.txt")" "$(cat "$tmp/vm/gone")"
check "with no temp file left" "$(cd "$d" && echo .[!.]* *)" ".[!.]* hold-prs.txt synced-at"
exit $fail

#!/bin/bash
# sync-from-vm.sh: refresh the mini's read-only copy of the VM's holds file, ~/onigiri/sync/hold-prs.txt, for pi
# sessions here. hosts/mini/host.sh runs it at every launch, so launches overlap. Each run holds an exclusive lock on
# $d/.lock from fetch to rename, so a slow fetch never replaces a newer one, and fetches into its own temp file, so
# nothing is left half-written when a run dies. A failed fetch keeps the old copy and exits 1. It reaches the VM
# through ./vm, or $ONIGIRI_VM when set (the test's fake).
set -u
d=$HOME/onigiri/sync; mkdir -p "$d"
if [ -z "${SYNC_FROM_VM_LOCKED:-}" ]; then
  # macOS has no flock(1); python3 takes the lock and execs this script again with the lock fd open. A run stuck on
  # ssh would hold it, so a launch waits at most 60s and then keeps the old copy.
  SYNC_FROM_VM_LOCKED=1 exec python3 -c 'import fcntl, os, sys, time
fd, end = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o644), time.time() + 60
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB); break
    except BlockingIOError:
        if time.time() > end:
            sys.exit("sync-from-vm: another sync holds the lock, keeping old copy")
        time.sleep(0.1)
os.set_inheritable(fd, True); os.execv(sys.argv[2], sys.argv[2:])' "$d/.lock" "$0" "$@"
fi
new=$(mktemp "$d/.hold-prs.txt.XXXXXX") || exit 1
stamp=; trap 'rm -f "$new" ${stamp:+"$stamp"}' EXIT
if ! "${ONIGIRI_VM:-${BASH_SOURCE[0]%/*}/vm}" "cat /srv/onigiri/hold-prs.txt" > "$new" || [ ! -s "$new" ]; then
  echo "sync-from-vm: hold-prs.txt failed, keeping old copy" >&2; exit 1
fi
chmod 444 "$new" && mv -f "$new" "$d/hold-prs.txt" || exit 1
stamp=$(mktemp "$d/.synced-at.XXXXXX") || exit 1
TZ=Asia/Singapore date '+%F %T SGT' > "$stamp" && chmod 644 "$stamp" && mv -f "$stamp" "$d/synced-at"

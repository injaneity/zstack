#!/bin/bash
set -u
d=$HOME/onigiri/sync; mkdir -p "$d"
if [ -z "${SYNC_FROM_VM_LOCKED:-}" ]; then
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

#!/bin/bash
# sync-from-vm.sh: refresh the mini's read-only copy of the VM's holds file, ~/onigiri/sync/hold-prs.txt, for pi
# sessions here. hosts/mini/host.sh runs it at every launch, so launches overlap: each run fetches into its own temp
# file and renames it over the copy, and a failed fetch keeps the old copy and exits 1. It reaches the VM through
# ./vm, or $ONIGIRI_VM when set (the test's fake).
set -u
d=$HOME/onigiri/sync; mkdir -p "$d"
new=$(mktemp "$d/.hold-prs.txt.XXXXXX") || exit 1
stamp=; trap 'rm -f "$new" ${stamp:+"$stamp"}' EXIT
if ! "${ONIGIRI_VM:-${BASH_SOURCE[0]%/*}/vm}" "cat /srv/onigiri/hold-prs.txt" > "$new" || [ ! -s "$new" ]; then
  echo "sync-from-vm: hold-prs.txt failed, keeping old copy" >&2; exit 1
fi
chmod 444 "$new" && mv -f "$new" "$d/hold-prs.txt" || exit 1
stamp=$(mktemp "$d/.synced-at.XXXXXX") || exit 1
TZ=Asia/Singapore date '+%F %T SGT' > "$stamp" && chmod 644 "$stamp" && mv -f "$stamp" "$d/synced-at"

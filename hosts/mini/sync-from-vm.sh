#!/bin/bash
# read-only copies of the VM's holds and hands-off list for pi sessions on the mini host.
d=$HOME/onigiri/sync; mkdir -p $d; rc=0
for f in hold-prs.txt; do
  if ~/onigiri-vm/vm "cat /srv/onigiri/$f" > $d/$f.new && [ -s $d/$f.new ]; then
    chmod u+w $d/$f 2>/dev/null || true; mv -f $d/$f.new $d/$f; chmod a-w $d/$f
  else echo "sync-from-vm: $f failed, keeping old copy" >&2; rm -f $d/$f.new; rc=1; fi
done
TZ=Asia/Singapore date '+%F %T SGT' > $d/synced-at; exit $rc

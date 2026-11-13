#!/bin/sh
set -u
export TMPDIR=/var/tmp/onigiri; mkdir -p "$TMPDIR"
name=$1 interval=$2 limit=$3; shift 3
dir=${ONIGIRI_ROOT:-/srv/onigiri}/tick; stamp=$dir/cron-$name.started; log=$dir/cron.log
now=$(date +%s); last=$(cat "$stamp" 2>/dev/null || echo 0)
[ $((now - last)) -ge $((interval - 30)) ] || exit 0
exec 9>"$dir/cron-$name.lock"
flock -n 9 || { echo "$(date -Is) $name skip: previous run still holds the lock" >>"$log"; exit 0; }
gap=$(( (now - last) / 60 ))
echo "$now" >"$stamp"
echo "$(date -Is) $name start (last start ${gap}m ago)" >>"$log"
timeout -k 60 "$limit" "$@"; rc=$?
[ $rc -eq 124 ] && echo "$(date -Is) $name TIMEOUT after ${limit}s, killed" >>"$log"
echo "$(date -Is) $name end rc=$rc" >>"$log"

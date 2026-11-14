#!/usr/bin/env bash
set -u
R=${RB_DIR:?RB_DIR: the run dir on the VM}; OLD=$1; NEW=$2
TASKS=${TASKS:-"CDB-G02 CDB-G03 CDB-G04 MB-09 MB-10 MB-11 IR-01 IR-02 IR-03 IR-04"}
log() { echo "$(date -u +%FT%TZ) $*" >> $R/progress.log; }
( while sleep 60; do [ "$(df -BG --output=avail / | tail -1 | tr -dc 0-9)" -lt 20 ] || continue
    for r in $R/sessions/rb-l-*/runs/rb-l-*; do touch $r/STOP; done; log "disk guard: STOP under 20G"; break; done ) &
G=$!
log "start linux lane old=$OLD new=$NEW harness $(git -C $R/wt rev-parse --short HEAD) P1=${P1:-5}"
i=0
for t in $TASKS; do
  if (( i % 2 )); then order="$NEW $OLD"; else order="$OLD $NEW"; fi; i=$((i+1))
  for v in $order; do
    id=rb-l-$v-$t
    [ -f $R/sessions/$id/runs/$id/DONE ] && { log "skip $id (DONE)"; continue; }
    pgrep -f "run-id $id" >/dev/null && { log "refuse $id: already running"; exit 1; }
    log "start $id"
    $(dirname "$(readlink -f "$0")")/linux-session.sh $v $t $id > $R/logs/$id.log 2>&1; rc=$?
    log "end $id rc=$rc done=$([ -f $R/sessions/$id/runs/$id/DONE ] && echo 1 || echo 0)"
    [ -f $R/sessions/$id/runs/$id/STOP ] && { log "stopped at $id"; kill $G; exit 1; }
  done
done
kill $G 2>/dev/null
log "linux lane done"

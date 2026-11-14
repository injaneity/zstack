#!/bin/zsh
set -u
OLD=$1; NEW=$2; L=~/rb/progress.log; W=~/bench-work
log() { echo "$(date -u +%FT%TZ) $*" >> $L; }
FG=(${=FG_TASKS:-CDB-G02 CDB-G03 CDB-G04 MB-09 MB-10 MB-11 IR-01 IR-02 IR-03 IR-04})
BG=(${=BG_TASKS:-CDB-G02 CDB-G03 CDB-G04 MB-09 IR-01 IR-02 IR-03 IR-04})
log "start macos lane old=$OLD new=$NEW harness $(cat $W/src/HARNESS_SHA) P1=${P1:-5}"
installed=""; i=0; first=""
for c in foreground background; do
  if [ $c = foreground ]; then tasks=($FG); else tasks=($BG); fi
  for t in $tasks; do
    if (( i % 2 )); then order=($NEW $OLD); else order=($OLD $NEW); fi; i=$((i+1))
    for v in $order; do
      R=rb-m-${c[1]}g-$v-$t
      if [ -f $W/runs/$R/DONE ]; then log "skip $R (DONE)"; continue; fi
      pgrep -f "run-id $R" >/dev/null && { log "refuse $R: already running"; exit 1; }
      if [ "$installed" != $v ]; then
        zsh ~/dlm/guest/install-driver.sh ~/rb/drv-$v > ~/rb/install-$v.log 2>&1 || { log "install $v failed"; exit 1; }
        installed=$v
      fi
      df -g / | awk 'NR==2 && $4 < 15 {exit 1}' || { log "disk under 15G, stop"; exit 1; }
      pf=0; [ -f ~/rb/pf-$v-$c.done ] || pf=1
      log "start $R"
      PREFLIGHT=$pf zsh ~/rb/guest/session.sh ~/rb/drv-$v $t $c $R > ~/rb/$R.log 2>&1; rc=$?
      [ $rc = 0 ] && touch ~/rb/pf-$v-$c.done
      log "end $R rc=$rc done=$([ -f $W/runs/$R/DONE ] && echo 1 || echo 0)"
      [ -f $W/runs/$R/STOP ] && { log "stopped at $R"; exit 1; }
    done
  done
done
log "macos lane done"

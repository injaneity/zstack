#!/bin/bash
set -u
H=$(cd "$(dirname "$0")" && pwd); export RB_DIR=$1; VR=$2; OLD=$3; NEW=$4
log() { echo "$(date -u +%FT%TZ) $*" >> "$RB_DIR/serial.log"; }
V=$H/../vm
log "start mac lane $OLD $NEW"
"$H/ssh.sh" "P1=${P1:-5} zsh ~/rb/guest/run-all.sh $OLD $NEW >> ~/rb/run-all.out 2>&1"; log "mac lane exit $?"
"$H/ssh.sh" 'tail -1 ~/rb/progress.log' >> "$RB_DIR/serial.log"
log "start linux lane $OLD $NEW"
"$V" "RB_DIR=$VR P1=${P1:-5} \$HOME/.pi/agent/git/github.com/injaneity/zstack/hosts/mini/release-bench/linux-all.sh $OLD $NEW >> $VR/linux-all.out 2>&1"
log "linux lane exit $?"
"$V" "tail -1 $VR/progress.log" >> "$RB_DIR/serial.log"
log "serial done"

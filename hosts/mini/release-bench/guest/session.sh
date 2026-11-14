#!/bin/zsh
set -u
D=${1:A}; TASK=$2; COND=$3; RUN_ID=$4
W=~/bench-work; MB=$W/src/macos_bench
source ~/.zprofile
export CDB_BENCH_WORK=$W CDB_TASKPACK=$W/taskpack CDB_BENCH_DISPOSABLE=1
export CDB_PI_PROVIDER=openai-codex CDB_PI_MODEL=gpt-6.1-terra CDB_PI_BIN=/opt/homebrew/bin/pi
unset CLAUDE_CODE_OAUTH_TOKEN CDB_CLAUDE_TOKEN_FILE
cd $MB
ARGS=(--bench-v2 --arms pi-cua-driver-main --pins $D/pins.json --build-dir $W/build
      --runs-dir $W/runs --disk-floor-gb 15 --allow-non-terminal)
if [ "${PREFLIGHT:-0}" = 1 ] && [ ! -f $W/runs/$RUN_ID/results.jsonl ]; then
  /opt/homebrew/bin/python3 run_bench.py preflight $ARGS --tasks $TASK --run-id $RUN_ID-preflight || exit 2
fi
/usr/bin/caffeinate -dimsu -w $$ &
/opt/homebrew/bin/python3 run_bench.py run $ARGS --tasks $TASK --condition $COND --park-pointer \
  --phase1-runs ${P1:-5} --phase2-runs 0 --run-id $RUN_ID

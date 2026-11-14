#!/usr/bin/env bash
set -u
R=${RB_DIR:?RB_DIR: the run dir on the VM}
MB=$R/wt/libs/cua-driver-bench/automated-eval/macos_bench
V=$1; TASK=$2; RUN_ID=$3; EV=$R/sessions/$RUN_ID
export CDB_BENCH_WORK=$R/work CDB_TASKPACK=$R/work/taskpack CDB_BENCH_DISPOSABLE=1
export CDB_PI_PROVIDER=openai-codex CDB_PI_MODEL=gpt-6.1-terra CDB_PI_BIN=$HOME/.local/bin/pi
export CUA_DRIVER_BIN=$R/drv-$V/cua-driver CUA_SKILLS=$R/drv-$V/skills
export PATH=$R/shim:$HOME/.nvm/versions/node/v22.23.3/bin:$HOME/.local/bin:$PATH
export RB_PKG=${RB_PKG:-$(dirname "$(readlink -f "$0")")}
export TAG_SHA=$(cat $R/drv-$V/GIT_SHA)
export RUN_ID TASK P1=${P1:-5} ARMS=pi-cua-driver-main
unset CLAUDE_CODE_OAUTH_TOKEN CDB_CLAUDE_TOKEN_FILE
mkdir -p "$EV"
cd "$MB"
exec bwrap --dev-bind / / --tmpfs /tmp --tmpfs /dev/shm --unshare-pid --proc /proc --die-with-parent -- \
 bash launch/linux_session.sh --evidence "$EV" --check-run "$EV/runs/$RUN_ID" -- bash -c '
  set -e
  python3 desktop_runtime.py doctor --cua-bin "$CUA_DRIVER_BIN" --out "$CDB_SESSION_DIR" --json > "$CDB_SESSION_DIR/doctor.stdout"
  python3 "$RB_PKG"/release_pins.py "$CUA_DRIVER_BIN" "$CUA_SKILLS" \
    "$CDB_SESSION_DIR/doctor.json" "$CDB_SESSION_DIR/pins.json" "$TAG_SHA"
  COMMON="--bench-v2 --arms $ARMS --cua-bin $CUA_DRIVER_BIN --cua-skills $CUA_SKILLS --pins $CDB_SESSION_DIR/pins.json --disk-floor-gb 15"
  if [ ! -f "$CDB_SESSION_DIR/runs/$RUN_ID/results.jsonl" ]; then
    python3 run_bench.py preflight $COMMON --tasks $TASK --runs-dir "$CDB_SESSION_DIR/runs" --run-id "$RUN_ID-preflight"
  fi
  python3 run_bench.py run $COMMON --tasks $TASK --condition foreground --phase1-runs $P1 --phase2-runs 0 --run-id "$RUN_ID" --runs-dir "$CDB_SESSION_DIR/runs"'

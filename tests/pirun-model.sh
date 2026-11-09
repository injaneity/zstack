#!/bin/bash
# Model-checks bin/PiRun.tla with TLC (every rule in bin/PiRun.cfg must hold) and fails when bin/PiRun.mmd is not the
# chart the model draws. tests/test_pi_run.py then holds bin/pi-run's step() to that chart, so the model, the chart
# and the launcher cannot drift apart unnoticed.
# usage: tests/pirun-model.sh   (TLA2TOOLS=<tla2tools.jar> and JAVA=<java> skip the statechart skill's own install)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
"$src/skills/statechart/scripts/statechart" "$src/bin/PiRun.tla" --view "$src/bin/PiRun.view.mts" --out "$tmp/out" > "$tmp/report" 2>&1
rc=$?
cat "$tmp/report"
[ $rc -eq 0 ] || { echo "FAIL  TLC did not pass bin/PiRun.tla (exit $rc)"; exit 1; }
grep -q '^## Rules that hold' "$tmp/report" || { echo "FAIL  the report names no rules that hold"; exit 1; }
diff -u "$src/bin/PiRun.mmd" "$tmp/out/chart.mmd" || { echo "FAIL  bin/PiRun.mmd is not the chart bin/PiRun.tla draws; copy the new chart in"; exit 1; }
echo "ok    bin/PiRun.tla holds and bin/PiRun.mmd is its chart"

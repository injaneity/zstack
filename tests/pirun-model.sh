#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
"$src/skills/statechart/scripts/statechart" "$src/bin/PiRun.tla" --view "$src/bin/PiRun.view.mts" --out "$tmp/full" > "$tmp/report" 2>&1
rc=$?
cat "$tmp/report"
[ $rc -eq 0 ] || { echo "FAIL  TLC did not pass bin/PiRun.tla (exit $rc)"; exit 1; }
grep -q '^## Rules that hold' "$tmp/report" || { echo "FAIL  the report names no rules that hold"; exit 1; }
"$src/skills/statechart/scripts/statechart" "$src/bin/PiRun.tla" --config "$src/bin/PiRunChart.cfg" --view "$src/bin/PiRun.view.mts" \
  --out "$tmp/out" > "$tmp/chart.report" 2>&1 || { cat "$tmp/chart.report"; echo "FAIL  statechart did not pass bin/PiRunChart.cfg"; exit 1; }
diff -u "$src/bin/PiRun.mmd" "$tmp/out/chart.mmd" || { echo "FAIL  bin/PiRun.mmd is not the chart bin/PiRunChart.cfg draws; copy the new chart in"; exit 1; }
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
python3 -I "$src/bin/PiRun.mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a bin/PiRun.mutants.py mutant passes TLC: the model cannot see that bug"; exit 1; }
echo "ok    bin/PiRun.tla holds, bin/PiRun.mmd is its chart, and every mutant fails"

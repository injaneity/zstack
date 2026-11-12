#!/bin/bash
# Model-checks model/Hourly.tla, the hourly pickup: TLC passes every rule in model/Hourly.cfg (the safety rules at the
# real caps) and model/HourlyLive.cfg (every pass ends), model/Hourly.mmd is the chart model/HourlyChart.cfg draws, every mutant in model/hourly_mutants.py fails on the rule it names (three put back
# zstack main 4e08648, before the pickup fix), and model/QuickLane.cfg (a probe) still breaks on QuickOutsideCap.
# A change to bin/hourly's pass or the hourly playbook's steps 0 to 5 changes this model in the same PR.
# usage: tests/hourly-model.sh   (TLA2TOOLS=<tla2tools.jar> and JAVA=<java> skip the mise lookup)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
cp "$src"/model/Hourly.tla "$src"/model/*.cfg "$tmp/"
caps="Total = $("$src/bin/policy" get slots.total) Auto = $("$src/bin/policy" get slots.auto)"
grep -q "$caps " "$src/model/Hourly.cfg" ||
  { echo "FAIL  model/Hourly.cfg does not check config/policy.toml's caps ($caps)"; exit 1; }
tlc() { (cd "$tmp" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers 2 -metadir "$tmp/meta-$1" -config "$1.cfg" Hourly.tla); }
tlc Hourly > "$tmp/hourly.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/hourly.out" ||
  { tail -30 "$tmp/hourly.out"; echo "FAIL  TLC did not pass model/Hourly.cfg"; exit 1; }
grep -E "distinct states found" "$tmp/hourly.out" | tail -1
tlc HourlyLive > "$tmp/live.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/live.out" ||
  { tail -30 "$tmp/live.out"; echo "FAIL  TLC did not pass model/HourlyLive.cfg (every pass ends)"; exit 1; }
tlc QuickLane > "$tmp/quick.out" 2>&1
grep -q "Invariant QuickOutsideCap is violated" "$tmp/quick.out" ||
  { tail -30 "$tmp/quick.out"; echo "FAIL  model/QuickLane.cfg no longer breaks on QuickOutsideCap"; exit 1; }
echo "ok    model/QuickLane.cfg breaks on QuickOutsideCap, as a probe should"
"$src/skills/statechart/scripts/statechart" "$src/model/Hourly.tla" --config "$src/model/HourlyChart.cfg" \
  --view "$src/model/Hourly.view.mts" --out "$tmp/chart" > "$tmp/chart.report" 2>&1 ||
  { cat "$tmp/chart.report"; echo "FAIL  statechart did not pass model/HourlyChart.cfg"; exit 1; }
diff -u "$src/model/Hourly.mmd" "$tmp/chart/chart.mmd" ||
  { echo "FAIL  model/Hourly.mmd is not the chart model/HourlyChart.cfg draws; copy the new chart in"; exit 1; }
echo "ok    model/Hourly.mmd is the chart"
python3 -I "$src/model/hourly_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/hourly_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Hourly.tla holds, and every mutant fails on its rule"

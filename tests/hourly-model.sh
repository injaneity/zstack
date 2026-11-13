#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
cp "$src"/model/Hourly.tla "$src"/model/*.cfg "$tmp/"
caps="Auto = $("$src/bin/policy" get slots.auto) Hand = $("$src/bin/policy" get slots.hand)"
grep -q "$caps " "$src/model/Hourly.cfg" ||
  { echo "FAIL  model/Hourly.cfg does not check config/policy.toml's caps ($caps)"; exit 1; }
tlc() { (cd "$tmp" && "$java" -XX:+UseParallelGC -jar "$jar" -workers 2 -metadir "$tmp/meta-$1" -config "$1.cfg" Hourly.tla); }
tlc Hourly > "$tmp/hourly.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/hourly.out" ||
  { tail -30 "$tmp/hourly.out"; echo "FAIL  TLC did not pass model/Hourly.cfg"; exit 1; }
grep -E "distinct states found" "$tmp/hourly.out" | tail -1
tlc HourlyLive > "$tmp/live.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/live.out" ||
  { tail -30 "$tmp/live.out"; echo "FAIL  TLC did not pass model/HourlyLive.cfg (every pass ends)"; exit 1; }
"$src/skills/statechart/scripts/statechart" "$src/model/Hourly.tla" --config "$src/model/HourlyChart.cfg" \
  --view "$src/model/Hourly.view.mts" --out "$tmp/chart" > "$tmp/chart.report" 2>&1 ||
  { cat "$tmp/chart.report"; echo "FAIL  statechart did not pass model/HourlyChart.cfg"; exit 1; }
diff -u "$src/model/Hourly.mmd" "$tmp/chart/chart.mmd" ||
  { echo "FAIL  model/Hourly.mmd is not the chart model/HourlyChart.cfg draws; copy the new chart in"; exit 1; }
echo "ok    model/Hourly.mmd is the chart"
python3 -I "$src/model/hourly_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/hourly_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Hourly.tla holds, and every mutant fails on its rule"

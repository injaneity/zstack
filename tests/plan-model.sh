#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
cp "$src"/model/Plan.tla "$src"/model/Plan.cfg "$tmp/"
(cd "$tmp" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers auto -metadir "$tmp/meta" -config Plan.cfg Plan.tla) > "$tmp/out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/out" || { tail -40 "$tmp/out"; echo "FAIL  TLC did not pass model/Plan.cfg"; exit 1; }
echo "ok    model/Plan.cfg: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
python3 -I "$src/model/plan_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/plan_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Plan.tla holds, and every mutant fails on its rule"

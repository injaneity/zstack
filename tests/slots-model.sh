#!/bin/bash
# Model-checks model/Slots.tla, the auto and hand slot pools: TLC passes every rule in model/Slots.cfg, and every mutant
# in model/slots_mutants.py makes it fail with the rule it names.
# usage: tests/slots-model.sh   (TLA2TOOLS=<tla2tools.jar> and JAVA=<java> skip the mise lookup)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
cp "$src"/model/Slots.tla "$src"/model/Slots.cfg "$tmp/"
(cd "$tmp" && "$java" -XX:+UseParallelGC -jar "$jar" -workers 4 -metadir "$tmp/meta" -config Slots.cfg Slots.tla) > "$tmp/slots.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/slots.out" ||
  { tail -30 "$tmp/slots.out"; echo "FAIL  TLC did not pass model/Slots.cfg"; exit 1; }
grep -E "distinct states found" "$tmp/slots.out" | tail -1
python3 -I "$src/model/slots_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/slots_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Slots.tla holds, and every mutant fails on its rule"

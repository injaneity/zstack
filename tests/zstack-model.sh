#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
jar=${TLA2TOOLS:-$(mise where "http:tla2tools[url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]@1.7.4")/tla2tools.jar}
java=${JAVA:-$(mise which java --tool java@temurin-25)}
cp "$src"/model/Zstack.tla "$src"/model/*.cfg "$tmp/"
tlc() { (cd "$tmp" && "$java" -cp "$jar" tlc2.TLC -workers 2 -metadir "$tmp/meta-$1" -config "$1.cfg" Zstack.tla); }
tlc Zstack > "$tmp/zstack.out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/zstack.out" ||
  { tail -30 "$tmp/zstack.out"; echo "FAIL  TLC did not pass model/Zstack.cfg"; exit 1; }
grep -E "distinct states found" "$tmp/zstack.out" | tail -1
tlc Race > "$tmp/race.out" 2>&1
grep -q "Invariant HeldIsNeverMergedStrict is violated" "$tmp/race.out" ||
  { tail -30 "$tmp/race.out"; echo "FAIL  model/Race.cfg no longer breaks on HeldIsNeverMergedStrict"; exit 1; }
echo "ok    model/Race.cfg breaks on HeldIsNeverMergedStrict, as a probe should"
python3 -I "$src/model/mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Zstack.tla holds, and every mutant fails on its rule"

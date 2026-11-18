#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ -n "${JAVA:-}" ]; then
  java=$JAVA jar=${TLA2TOOLS:?names tla2tools.jar when JAVA is set} by=TLC
else
  "$src/bin/everystate" -help > /dev/null || { echo "FAIL  no checker: see the line above"; exit 1; }
  java=$src/bin/everystate-java jar=unused by=everystate
fi
for cfg in Queue.cfg Queue.live.cfg; do
  mkdir "$tmp/$cfg" && cp "$src/model/Queue.tla" "$tmp/$cfg/" && cp "$src/model/$cfg" "$tmp/$cfg/Queue.cfg"
  (cd "$tmp/$cfg" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
  grep -q "Model checking completed. No error has been found." "$tmp/out" || { tail -40 "$tmp/out"; echo "FAIL  $by did not pass model/$cfg"; exit 1; }
  echo "ok    model/$cfg: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
done
python3 -I "$src/model/queue_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/queue_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Queue.tla holds under $by, and every mutant fails on its rule"

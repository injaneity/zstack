#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
checker=$src/bin/everystate
"$checker" -help > /dev/null || { echo "FAIL  no checker: see the line above"; exit 1; }
mkdir "$tmp/spec" && cp "$src/model/Queue.tla" "$src/model/Queue.cfg" "$tmp/spec/"
(cd "$tmp/spec" && "$checker" -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/out" || { tail -40 "$tmp/out"; echo "FAIL  everystate did not pass model/Queue.cfg"; exit 1; }
echo "ok    model/Queue.cfg: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
mkdir "$tmp/direct" && cp "$src/model/Queue.tla" "$tmp/direct/" && sed 's/^  Queues = {"i1"}$/  Queues = {}/' "$src/model/Queue.cfg" > "$tmp/direct/Queue.cfg"
grep -q '^  Queues = {}$' "$tmp/direct/Queue.cfg" || { echo "FAIL  model/Queue.cfg has no Queues = {\"i1\"} line to clear"; exit 1; }
(cd "$tmp/direct" && "$checker" -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/out" || { tail -40 "$tmp/out"; echo "FAIL  everystate did not pass model/Queue.cfg with no merge queue"; exit 1; }
echo "ok    model/Queue.cfg with no merge queue: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
mkdir "$tmp/resume" && cp "$src/model/Queue.tla" "$tmp/resume/"
sed -e 's/^  Machines = {"mac", "linux"}$/  Machines = {"mac"}/' -e 's/^  Resumes = {}$/  Resumes = {"i1", "i2", "i3"}/' -e 's/^  Retries = {}$/  Retries = {"i1"}/' \
  -e 's/^  Fills = {}$/  Fills = {"mac"}/' "$src/model/Queue.cfg" > "$tmp/resume/Queue.cfg"
[ "$(grep -cxE '  (Machines = \{"mac"\}|Resumes = \{"i1", "i2", "i3"\}|Retries = \{"i1"\}|Fills = \{"mac"\})' "$tmp/resume/Queue.cfg")" = 4 ] ||
  { echo "FAIL  model/Queue.cfg has no Machines, Resumes, Retries and Fills lines to set"; exit 1; }
(cd "$tmp/resume" && "$checker" -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/out" ||
  { tail -40 "$tmp/out"; echo "FAIL  everystate did not pass model/Queue.cfg on one machine that resumes every item, retries a dropped head and falls below its disk floor"; exit 1; }
echo "ok    model/Queue.cfg on one machine that resumes every item, retries a dropped head and falls below its disk floor: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
mkdir "$tmp/red" && cp "$src/model/Queue.tla" "$tmp/red/"
sed -e 's/^  Machines = {"mac", "linux"}$/  Machines = {"mac"}/' -e 's/^  Resumes = {}$/  Resumes = {"i1", "i2", "i3"}/' -e 's/^  Retries = {}$/  Retries = {"i1"}/' \
  -e 's/^  Fills = {}$/  Fills = {"mac"}/' -e 's/^  Hooked = {"i3"}$/  Hooked = {"i1", "i3"}/' -e 's/^  Reds = {}$/  Reds = {"i1"}/' "$src/model/Queue.cfg" > "$tmp/red/Queue.cfg"
[ "$(grep -cxE '  (Machines = \{"mac"\}|Resumes = \{"i1", "i2", "i3"\}|Retries = \{"i1"\}|Fills = \{"mac"\}|Hooked = \{"i1", "i3"\}|Reds = \{"i1"\})' "$tmp/red/Queue.cfg")" = 6 ] ||
  { echo "FAIL  model/Queue.cfg has no Machines, Resumes, Retries, Fills, Hooked and Reds lines to set"; exit 1; }
(cd "$tmp/red" && "$checker" -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
grep -q "Model checking completed. No error has been found." "$tmp/out" ||
  { tail -40 "$tmp/out"; echo "FAIL  everystate did not pass model/Queue.cfg on one machine whose resumed item logs its pushes and goes red"; exit 1; }
echo "ok    model/Queue.cfg on one machine whose resumed item logs its pushes and goes red: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
python3 -I "$src/model/queue_mutants.py" "$checker" "$tmp/mutants" || { echo "FAIL  a model/queue_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Queue.tla holds under everystate, and every mutant fails on its rule"

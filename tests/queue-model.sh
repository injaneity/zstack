#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
if [ -n "${JAVA:-}" ]; then
  java=$JAVA jar=${TLA2TOOLS:?names tla2tools.jar when JAVA is set} by=TLC
else
  rev=c1544c8dc536fb3460badab96db354f1165700ed
  root=${XDG_CACHE_HOME:-$HOME/.cache}/zstack/everystate-$rev
  [ -x "$root/bin/everystate" ] || PATH="$HOME/.cargo/bin:$PATH" cargo install --quiet --locked --git https://github.com/injaneity/everystate \
    --rev "$rev" --root "$root" everystate >&2 || { echo "FAIL  could not build everystate: install Rust, or set JAVA and TLA2TOOLS to check with TLC"; exit 1; }
  printf '#!/bin/sh\nexec "%s" java "$@"\n' "$root/bin/everystate" > "$tmp/java" && chmod +x "$tmp/java"
  java=$tmp/java jar=unused by=everystate
fi
for cfg in Queue.cfg Queue.live.cfg; do
  mkdir "$tmp/$cfg" && cp "$src/model/Queue.tla" "$tmp/$cfg/" && cp "$src/model/$cfg" "$tmp/$cfg/Queue.cfg"
  (cd "$tmp/$cfg" && "$java" -XX:+UseParallelGC -cp "$jar" tlc2.TLC -workers auto -metadir meta -config Queue.cfg Queue.tla) > "$tmp/out" 2>&1
  grep -q "Model checking completed. No error has been found." "$tmp/out" || { tail -40 "$tmp/out"; echo "FAIL  $by did not pass model/$cfg"; exit 1; }
  echo "ok    model/$cfg: $(grep -E 'distinct states found' "$tmp/out" | tail -1)"
done
python3 -I "$src/model/queue_mutants.py" "$jar" "$java" "$tmp/mutants" || { echo "FAIL  a model/queue_mutants.py mutant was not killed by its rule"; exit 1; }
echo "ok    model/Queue.tla holds under $by, and every mutant fails on its rule"

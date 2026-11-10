#!/bin/bash
# Checks bin/model-for against config/models.json.
# usage: tests/models.sh (exit 0 when every check passes)
set -u
pkg="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
cfg_of() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1]))["roles"][sys.argv[2]]; print(r["provider"], r["model"])' "$pkg/config/models.json" "$1"; }

for role in coding research explorer verifier; do
  check "model-for $role matches the config" "$("$pkg/bin/model-for" "$role")" "$(cfg_of "$role")"
done
check "designer seats match the config" "$("$pkg/bin/model-for" designer:1)|$("$pkg/bin/model-for" designer:2)" "$(python3 -I -c 'import json,sys; print("|".join("%s %s" % (s["provider"], s["model"]) for s in json.load(open(sys.argv[1]))["roles"]["designer"]))' "$pkg/config/models.json")"
err=$("$pkg/bin/model-for" designer 2>&1 >/dev/null); check "a role with several seats needs a seat number" "$?" 1
check "model-for prints its zstack sha on stderr" "$(echo "$err" | head -1)" "zstack $(git -C "$pkg" rev-parse HEAD)"
err=$(echo "$err" | tail -n +2)
check "bare designer names both seats" "$err" "model-for: designer has 2 seats, one per side of a race; pass one of designer:1, designer:2. A race runs every seat."
err=$("$pkg/bin/model-for" coding:1 2>&1 >/dev/null); check "a one-seat role takes no seat number" "$?" 1
"$pkg/bin/model-for" no-such-role >/dev/null 2>&1; check "model-for rejects an unknown role" "$?" 1
"$pkg/bin/model-for" review >/dev/null 2>&1; check "review is not a role: verification is the verifier's alone" "$?" 1

exit $fail

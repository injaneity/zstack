#!/bin/bash
set -u
pkg="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
cfg_of() { echo "$("$pkg/bin/policy" get "models.$1.provider") $("$pkg/bin/policy" get "models.$1.model")"; }

for role in coding research explorer verifier; do
  check "policy seat $role matches the config" "$("$pkg/bin/policy" seat "$role")" "$(cfg_of "$role")"
done
check "designer seats match the config" "$("$pkg/bin/policy" seat designer:1)|$("$pkg/bin/policy" seat designer:2)" "$("$pkg/bin/policy" get models.designer | python3 -I -c 'import json,sys; print("|".join("%s %s" % (s["provider"], s["model"]) for s in json.load(sys.stdin)))')"
err=$("$pkg/bin/policy" seat designer 2>&1 >/dev/null); check "a role with several seats needs a seat number" "$?" 1
check "bare designer names both seats" "$err" "policy: designer has 2 seats, one per side of a race; pass one of designer:1, designer:2. A race runs every seat."
"$pkg/bin/policy" seat coding:1 >/dev/null 2>&1; check "a one-seat role takes no seat number" "$?" 1
"$pkg/bin/policy" seat no-such-role >/dev/null 2>&1; check "policy seat rejects an unknown role" "$?" 1
"$pkg/bin/policy" seat review >/dev/null 2>&1; check "review is not a role: verification is the verifier's alone" "$?" 1

exit $fail

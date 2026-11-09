#!/bin/bash
# Checks bin/model-for and bin/check-models against config/models.json and a fixture session tree.
# usage: tests/models.sh (exit 0 when every check passes)
set -u
pkg="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
cfg_of() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1]))["roles"][sys.argv[2]]; print(r["provider"], r["model"])' "$pkg/config/models.json" "$1"; }

for role in coding review research explorer verifier; do
  check "model-for $role matches the config" "$("$pkg/bin/model-for" "$role")" "$(cfg_of "$role")"
done
check "designer seats match the config" "$("$pkg/bin/model-for" designer:1)|$("$pkg/bin/model-for" designer:2)" "$(python3 -I -c 'import json,sys; print("|".join("%s %s" % (s["provider"], s["model"]) for s in json.load(open(sys.argv[1]))["roles"]["designer"]))' "$pkg/config/models.json")"
err=$("$pkg/bin/model-for" designer 2>&1 >/dev/null); check "a role with several seats needs a seat number" "$?" 1
check "bare designer names both seats" "$err" "model-for: designer has 2 seats, one per side of a race; pass one of designer:1, designer:2. A race runs every seat."
err=$("$pkg/bin/model-for" coding:1 2>&1 >/dev/null); check "a one-seat role takes no seat number" "$?" 1
"$pkg/bin/model-for" no-such-role >/dev/null 2>&1; check "model-for rejects an unknown role" "$?" 1

t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/review/good" "$t/review/wrong" "$t/review/old" "$t/review/none" "$t/review/kids" "$t/agent/sessions/x" "$t/agent/sessions/subagents"
read -r rp rm < <("$pkg/bin/model-for" explorer)
read -r cp cm < <("$pkg/bin/model-for" coding)
session() { # <id> <role or -> <provider> <model> [<cwd>] [<sessions subdir>]
  { echo "{\"type\":\"session\",\"id\":\"$1\",\"cwd\":\"${5:-/elsewhere}\"}"
    [ "$2" = - ] || echo "{\"type\":\"session_info\",\"name\":\"role:$2\"}"
    echo "{\"type\":\"model_change\",\"provider\":\"$3\",\"modelId\":\"$4\"}"
    echo "{\"type\":\"message\",\"message\":{\"role\":\"assistant\",\"provider\":\"$3\",\"model\":\"$4\",\"stopReason\":\"stop\"}}"
  } > "$t/agent/sessions/${6:-x}/$1.jsonl"
}
session good explorer "$rp" "$rm"
session wrong explorer "$cp" "$cm"
session old - "$cp" "$cm"
read -r d2p d2m < <("$pkg/bin/model-for" designer:2)
session kids coding "$cp" "$cm"
session kids-retry coding "$cp" "$cm" "$t/review/kids"
session kids-designer-2-a designer:2 "$d2p" "$d2m" "$t/review/kids" subagents
session kids-verifier-b verifier "$cp" "$cm" "$t/review/kids" subagents
session kids-explorer-c "explorer fallback" "$cp" "$cm" "$t/review/kids" subagents
echo '{"type":"session","id":"stray"}' > "$t/agent/sessions/x/stray.jsonl"  # no cwd: must not crash the scan
out=$(PI_CODING_AGENT_DIR="$t/agent" "$pkg/bin/check-models" "$t/review"); rc=$?
row() { echo "$out" | awk -v s="$1" '$1==s' ; }
check "check-models exits 1 on a mismatch" "$rc" 1
check "matching session is ok" "$(row good | grep -c ' ok$')" 1
check "session on the wrong model is a mismatch" "$(row wrong | grep -c MISMATCH)" 1
check "session without a role is a mismatch" "$(row old | grep -c MISMATCH)" 1
check "directory without a session is reported" "$(row none | grep -c 'no session file')" 1
crow() { echo "$out" | awk -v s="kids:$1" 'index($1, s)==1'; }
check "child on its seat is ok" "$(crow kids-designer | grep -c ' ok (child of kids)')" 1
check "child on the parent's model without a fallback is a mismatch" "$(crow kids-verifier | grep -c MISMATCH)" 1
check "child that fell back is reported as a fallback" "$(crow kids-explorer | grep -c FALLBACK)" 1
check "a sibling session whose id extends the parent's is not its child" "$(crow kids-retry | grep -c 'child of')" 0
check "three mismatches counted" "$(echo "$out" | tail -1)" "3 mismatch(es)"
exit $fail

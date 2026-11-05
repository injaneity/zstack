#!/bin/bash
# Checks bin/model-for and bin/check-models against config/models.json and a fixture session tree.
# usage: tests/models.sh (exit 0 when every check passes)
set -u
pkg="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
cfg_of() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1]))["roles"][sys.argv[2]]; print(r["provider"], r["model"])' "$pkg/config/models.json" "$1"; }

for role in coding review reads research; do
  check "model-for $role matches the config" "$("$pkg/bin/model-for" "$role")" "$(cfg_of "$role")"
done
check "reads defaults to openai-codex gpt-6.1-terra" "$("$pkg/bin/model-for" reads)" "openai-codex gpt-6.1-terra"
"$pkg/bin/model-for" no-such-role >/dev/null 2>&1; check "model-for rejects an unknown role" "$?" 1

t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/review/good" "$t/review/wrong" "$t/review/old" "$t/review/none" "$t/agent/sessions/x"
read -r rp rm < <("$pkg/bin/model-for" reads)
read -r cp cm < <("$pkg/bin/model-for" coding)
session() { # <id> <role or -> <provider> <model>
  { echo "{\"type\":\"session\",\"id\":\"$1\",\"cwd\":\"/elsewhere\"}"
    [ "$2" = - ] || echo "{\"type\":\"session_info\",\"name\":\"role:$2\"}"
    echo "{\"type\":\"model_change\",\"provider\":\"$3\",\"modelId\":\"$4\"}"
    echo "{\"type\":\"message\",\"message\":{\"role\":\"assistant\",\"provider\":\"$3\",\"model\":\"$4\",\"stopReason\":\"stop\"}}"
  } > "$t/agent/sessions/x/$1.jsonl"
}
session pi-good reads "$rp" "$rm"
session pi-wrong reads "$cp" "$cm"
session pi-old - "$cp" "$cm"
out=$(PI_CODING_AGENT_DIR="$t/agent" "$pkg/bin/check-models" "$t/review"); rc=$?
row() { echo "$out" | awk -v s="$1" '$1==s' ; }
check "check-models exits 1 on a mismatch" "$rc" 1
check "matching session is ok" "$(row good | grep -c ' ok$')" 1
check "session on the wrong model is a mismatch" "$(row wrong | grep -c MISMATCH)" 1
check "session without a role is unchecked" "$(row old | grep -c unchecked)" 1
check "directory without a session is reported" "$(row none | grep -c 'no session file')" 1
check "one mismatch counted" "$(echo "$out" | tail -1)" "1 mismatch(es)"
exit $fail

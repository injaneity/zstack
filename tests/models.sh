#!/bin/bash
# Checks bin/model-for and bin/check-models against config/models.json and a fixture session tree.
# usage: tests/models.sh (exit 0 when every check passes)
set -u
pkg="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
cfg_of() { python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1]))["roles"][sys.argv[2]]; print(r["provider"], r["model"])' "$pkg/config/models.json" "$1"; }

for role in builder explorer verifier; do
  check "model-for $role matches the config" "$("$pkg/bin/model-for" "$role")" "$(cfg_of "$role")"
done
check "the two designers are on different providers" "$("$pkg/bin/model-for" designer:1 | cut -d' ' -f1) $("$pkg/bin/model-for" designer:2 | cut -d' ' -f1)" "claude-code openai-codex"
for a in coding:builder reads:explorer review:verifier; do
  check "alias ${a%%:*} resolves to ${a#*:}" "$("$pkg/bin/model-for" "${a%%:*}")" "$("$pkg/bin/model-for" "${a#*:}")"
done
check "research keeps its own claude seat" "$("$pkg/bin/model-for" research)" "claude-code claude-opus-5-5"
check "explorer is openai-codex gpt-6.1-terra" "$("$pkg/bin/model-for" explorer)" "openai-codex gpt-6.1-terra"
err=$("$pkg/bin/model-for" designer 2>&1 >/dev/null); check "a role with several seats needs a seat number" "$?" 1
check "bare designer names both seats" "$err" "model-for: designer has 2 seats, one per side of a race; pass one of designer:1, designer:2. A race runs every seat."
err=$("$pkg/bin/model-for" coding:1 2>&1 >/dev/null); check "a one-seat role takes no seat number" "$?" 1
err=$(ZSTACK_ENSURED=1 PI_RUN_DRY=1 "$pkg/hosts/vm/pi-run" t-designer designer "$pkg/README.md" 2>&1); check "pi-run refuses bare designer" "$?" 2
check "pi-run says which seats exist" "$(echo "$err" | grep -c 'pass one of designer:1, designer:2')" 1
"$pkg/bin/model-for" no-such-role >/dev/null 2>&1; check "model-for rejects an unknown role" "$?" 1
# launchers take a role only: the old provider/model form is an unknown role, never a silent run as coding.
lt=$(mktemp -d); b=$lt/brief.md; echo brief > "$b"
err=$(ZSTACK_ENSURED=1 PI_RUN_DRY=1 "$pkg/hosts/vm/pi-run" t-legacy claude-code claude-opus-5-5 "$b" 2>&1); check "pi-run refuses the old provider/model form" "$?" 2
check "pi-run names the bad role" "$(echo "$err" | grep -c "no role 'claude-code'")" 1
check "pi-run no longer falls back to coding" "$(echo "$err" | grep -c 'running as role coding')" 0
err=$(ZSTACK_ENSURED=1 PI_RUN_DRY=1 "$pkg/hosts/vm/pi-run" t-missing coding "$b.missing" 2>&1); check "pi-run refuses a missing prompt file" "$?" 2
err=$(ZSTACK_ENSURED=1 PI_RUN_MINI_DRY=1 HOME="$lt" "$pkg/hosts/mini/pi-run-mini" t-legacy "$b" claude-opus-5-5 2>&1); check "pi-run-mini refuses a model id as its role" "$?" 2
check "pi-run-mini no longer falls back to coding" "$(echo "$err" | grep -c 'running as role coding')" 0
err=$(ZSTACK_ENSURED=1 PI_RUN_MINI_DRY=1 HOME="$lt" "$pkg/hosts/mini/pi-run-mini" t-missing "$b.missing" 2>&1); check "pi-run-mini refuses a missing brief" "$?" 2
check "pi-run-mini made no review dir for a missing brief" "$(ls "$lt/onigiri/review" 2>/dev/null)" ""
err=$(ZSTACK_ENSURED=1 PI_RUN_DRY=1 "$pkg/hosts/vm/pi-run" t-dir coding "$lt" 2>&1); check "pi-run refuses a directory as its prompt" "$?" 2
rm -rf "$lt"

t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/review/good" "$t/review/wrong" "$t/review/old" "$t/review/none" "$t/review/kids" "$t/agent/sessions/x" "$t/agent/sessions/subagents"
read -r rp rm < <("$pkg/bin/model-for" explorer)
read -r cp cm < <("$pkg/bin/model-for" builder)
session() { # <id> <role or -> <provider> <model> [<cwd>] [<sessions subdir>]
  { echo "{\"type\":\"session\",\"id\":\"$1\",\"cwd\":\"${5:-/elsewhere}\"}"
    [ "$2" = - ] || echo "{\"type\":\"session_info\",\"name\":\"role:$2\"}"
    echo "{\"type\":\"model_change\",\"provider\":\"$3\",\"modelId\":\"$4\"}"
    echo "{\"type\":\"message\",\"message\":{\"role\":\"assistant\",\"provider\":\"$3\",\"model\":\"$4\",\"stopReason\":\"stop\"}}"
  } > "$t/agent/sessions/${6:-x}/$1.jsonl"
}
session pi-good reads "$rp" "$rm"
session pi-wrong reads "$cp" "$cm"
session pi-old - "$cp" "$cm"
read -r d2p d2m < <("$pkg/bin/model-for" designer:2)
session pi-kids builder "$cp" "$cm"
session pi-kids-retry builder "$cp" "$cm" "$t/review/kids"
session pi-kids-designer-2-a designer:2 "$d2p" "$d2m" "$t/review/kids" subagents
session pi-kids-verifier-b verifier "$cp" "$cm" "$t/review/kids" subagents
session pi-kids-explorer-c "explorer fallback" "$cp" "$cm" "$t/review/kids" subagents
echo '{"type":"session","id":"stray"}' > "$t/agent/sessions/x/stray.jsonl"  # no cwd: must not crash the scan
out=$(PI_CODING_AGENT_DIR="$t/agent" "$pkg/bin/check-models" "$t/review"); rc=$?
row() { echo "$out" | awk -v s="$1" '$1==s' ; }
check "check-models exits 1 on a mismatch" "$rc" 1
check "matching session is ok" "$(row good | grep -c ' ok$')" 1
check "session on the wrong model is a mismatch" "$(row wrong | grep -c MISMATCH)" 1
check "session without a role is unchecked" "$(row old | grep -c unchecked)" 1
check "directory without a session is reported" "$(row none | grep -c 'no session file')" 1
crow() { echo "$out" | awk -v s="kids:$1" 'index($1, s)==1'; }
check "child on its seat is ok" "$(crow pi-kids-designer | grep -c ' ok (child of pi-kids)')" 1
check "child on the parent's model without a fallback is a mismatch" "$(crow pi-kids-verifier | grep -c MISMATCH)" 1
check "child that fell back is reported as a fallback" "$(crow pi-kids-explorer | grep -c FALLBACK)" 1
check "a sibling session whose id extends the parent's is not its child" "$(crow pi-kids-retry | grep -c 'child of')" 0
check "two mismatches counted" "$(echo "$out" | tail -1)" "2 mismatch(es)"
exit $fail

#!/bin/bash
# Checks bin/hard-rules prints onigiri-mode's "## Hard rules" section and nothing else, or fails without it.
# tests/test_pi_run.py checks bin/pi-run passes exactly that text to pi and starts nothing without it.
# usage: tests/hard-rules.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
lacks() { case "$2" in *"$3"*) echo "FAIL  $1: has '$3'"; fail=1;; *) echo "ok    $1";; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

rules=$("$src/bin/hard-rules"); check "hard-rules exits 0" $? 0
check "it starts at the Hard rules heading" "$(printf '%s\n' "$rules" | head -1)" "## Hard rules"
check "it holds no other ## section" "$(printf '%s\n' "$rules" | grep -c '^## ')" 1
lacks "it stops before Non-negotiables" "$rules" "## Non-negotiables"
lacks "it carries no playbook routing" "$rules" "## Playbooks"

# a copy of the package whose skill has no Hard rules section: hard-rules fails
mkdir -p "$tmp/pkg/bin" "$tmp/pkg/skills/onigiri-mode"
cp "$src/bin/hard-rules" "$tmp/pkg/bin/"
printf '# onigiri mode\n\n## Principles\n\n- x\n' > "$tmp/pkg/skills/onigiri-mode/SKILL.md"
"$tmp/pkg/bin/hard-rules" > /dev/null 2>&1; check "no section: hard-rules exits 1" $? 1
printf '# onigiri mode\n\n## Hard rules\n\n   \n## Principles\n\n- x\n' > "$tmp/pkg/skills/onigiri-mode/SKILL.md"
"$tmp/pkg/bin/hard-rules" > /dev/null 2>&1; check "heading only: hard-rules exits 1" $? 1

exit $fail

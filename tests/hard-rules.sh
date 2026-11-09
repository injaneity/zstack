#!/bin/bash
# Checks the session's appended prompt: bin/hard-rules prints onigiri-mode's "## Hard rules" section and nothing
# else, and bin/pi-run and hosts/vm/pi-run pass exactly that text to pi, or refuse to start without it.
# usage: tests/hard-rules.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: lacks '$3'"; fail=1;; esac; }
lacks() { case "$2" in *"$3"*) echo "FAIL  $1: has '$3'"; fail=1;; *) echo "ok    $1";; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

rules=$("$src/bin/hard-rules"); check "hard-rules exits 0" $? 0
check "it starts at the Hard rules heading" "$(printf '%s\n' "$rules" | head -1)" "## Hard rules"
check "it holds no other ## section" "$(printf '%s\n' "$rules" | grep -c '^## ')" 1
for s in "### Patent hold" "### Merging" "### Scope" "### Changing these rules" "In \`trycua/cua\` the tick is the only merger." \
  "A change whose diff touches pairing, Teleport or Keyvault is never pushed to public \`trycua/cua\`."; do
  has "it carries: $s" "$rules" "$s"
done
lacks "it stops before Non-negotiables" "$rules" "## Non-negotiables"
lacks "it carries no playbook routing" "$rules" "## Playbooks"

# a copy of the package whose skill has no Hard rules section: hard-rules fails, and pi-run starts nothing
mkdir -p "$tmp/pkg/bin" "$tmp/pkg/skills/onigiri-mode"
cp "$src/bin/hard-rules" "$src/bin/pi-run" "$tmp/pkg/bin/"
printf '# onigiri mode\n\n## Principles\n\n- x\n' > "$tmp/pkg/skills/onigiri-mode/SKILL.md"
"$tmp/pkg/bin/hard-rules" > /dev/null 2>&1; check "no section: hard-rules exits 1" $? 1
printf '# onigiri mode\n\n## Hard rules\n\n   \n## Principles\n\n- x\n' > "$tmp/pkg/skills/onigiri-mode/SKILL.md"
"$tmp/pkg/bin/hard-rules" > /dev/null 2>&1; check "heading only: hard-rules exits 1" $? 1

# fake pi records the --append-system-prompt it was given
mkdir -p "$tmp/bin"
cat > "$tmp/bin/pi" <<FAKE
#!/bin/bash
case "\$1" in list) echo "User packages:"; sed -n 's/^packages=("\([^"]*\)")\$/  \1/p' "$src/bin/ensure-extensions"; exit 0;; esac
while [ \$# -gt 0 ]; do [ "\$1" = --append-system-prompt ] && printf '%s' "\$2" > "$tmp/appended"; shift; done
FAKE
chmod +x "$tmp/bin/pi"; echo task > "$tmp/brief.md"
run() { HOME="$tmp" PATH="$tmp/bin:/usr/bin:/bin" ZSTACK_ENSURED=1 ONIGIRI_WAKE_DRY=1 ONIGIRI_ROOT="$tmp/root" "$@"; }

rm -f "$tmp/appended"; run "$src/bin/pi-run" s1 "$tmp/brief.md" > /dev/null 2>&1
check "bin/pi-run appends exactly the Hard rules" "$(cat "$tmp/appended" 2>/dev/null)" "$rules"
rm -f "$tmp/appended"; run "$src/hosts/vm/pi-run" vm-s1 coding "$tmp/brief.md" > /dev/null 2>&1
check "hosts/vm/pi-run appends exactly the Hard rules" "$(cat "$tmp/appended" 2>/dev/null)" "$rules"
rm -f "$tmp/appended"; run "$tmp/pkg/bin/pi-run" s2 "$tmp/brief.md" > /dev/null 2>&1; got=$?
check "no section: bin/pi-run exits 2" $got 2
check "no section: bin/pi-run starts no pi" "$([ -e "$tmp/appended" ] && echo started || echo none)" none
exit $fail

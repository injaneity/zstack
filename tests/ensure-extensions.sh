#!/bin/bash
# Checks bin/ensure-extensions against a fake `pi` on PATH. No network, no real pi.
# usage: tests/ensure-extensions.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: '$2' lacks '$3'"; fail=1;; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
src_pkg=$(sed -n 's/^packages=("\([^"]*\)")$/\1/p' "$src/bin/ensure-extensions")
[ -n "$src_pkg" ] || { echo "FAIL  cannot read the package list from bin/ensure-extensions"; exit 1; }

# fake pi: `list` prints what $tmp/installed holds in pi's format, `install` appends unless $tmp/fail-install exists.
mkdir -p "$tmp/bin"
cat > "$tmp/bin/pi" <<EOF
#!/bin/bash
case "\$1" in
  list) echo "User packages:"; while read -r s; do echo "  \$s"; echo "    /somewhere"; done < "$tmp/installed";;
  install) echo "\$2" >> "$tmp/calls"; [ -e "$tmp/fail-install" ] && { echo "npm ERR! 404"; exit 1; }
           [ -e "$tmp/lie" ] || echo "\$2" >> "$tmp/installed";;
esac
EOF
chmod +x "$tmp/bin/pi"
run() { PATH="$tmp/bin:/usr/bin:/bin" "$src/bin/ensure-extensions" 2>&1; }

echo "git:github.com/injaneity/zstack" > "$tmp/installed"; : > "$tmp/calls"
out=$(run); rc=$?
check "missing package installs and exits 0" "$rc" 0
check "missing package says it installed it" "$out" "extensions: installed $src_pkg"
check "pi install ran once with the pinned source" "$(cat "$tmp/calls")" "$src_pkg"

: > "$tmp/calls"; out=$(run); rc=$?
check "present package exits 0" "$rc" 0
check "present package says so" "$out" "extensions: $src_pkg present"
check "present package runs no install" "$(cat "$tmp/calls")" ""

echo "git:github.com/injaneity/zstack" > "$tmp/installed"; touch "$tmp/fail-install"
out=$(run); rc=$?
check "failed install exits 1" "$rc" 1
has "failed install names the source" "$out" "pi install $src_pkg failed"
has "failed install refuses the launch" "$out" "not launching without it"
rm "$tmp/fail-install"

touch "$tmp/lie"; out=$(run); rc=$?
check "install that does not land exits 1" "$rc" 1
has "install that does not land says so" "$out" "does not show it"
rm "$tmp/lie"

out=$(PATH="/nonexistent" /bin/bash "$src/bin/ensure-extensions" 2>&1); rc=$?
check "no pi on PATH exits 1" "$rc" 1
has "no pi on PATH says so" "$out" "pi is not on PATH"
exit $fail

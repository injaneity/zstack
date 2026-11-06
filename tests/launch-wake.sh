#!/bin/bash
# Runs both launchers against a fake `pi` and a temp HOME, with ONIGIRI_WAKE_DRY=1 so no POST leaves the machine.
# Checks each one fires one pi-done or pi-failed event after pi exits and keeps pi's exit code.
# usage: tests/launch-wake.sh (exit 0 when every check passes)
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: '$2' lacks '$3'"; fail=1;; esac; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/onigiri/bin" "$tmp/.cargo"
# fake pi: `list` shows the pinned extensions so ensure-extensions passes; a run exits with $tmp/rc and records
# that the launcher exported ONIGIRI_WAKE into its environment, which is how a session finds the sender.
cat > "$tmp/bin/pi" <<EOF
#!/bin/bash
case "\$1" in
  list) echo "User packages:"; sed -n 's/^packages=("\([^"]*\)")\$/  \1/p' "$src/bin/ensure-extensions";;
  *) echo "\$ONIGIRI_WAKE" > "$tmp/seen-wake"
     [ -e "$tmp/hang" ] && { trap 'echo term > "$tmp/got-term"; exit 143' TERM; while :; do sleep 0.1; done; }
     exit \$(cat "$tmp/rc");;
esac
EOF
chmod +x "$tmp/bin/pi"
: > "$tmp/onigiri/env.sh"; : > "$tmp/.cargo/env"
printf '#!/bin/sh\n' > "$tmp/onigiri/bin/sync-from-vm.sh"; chmod +x "$tmp/onigiri/bin/sync-from-vm.sh"
echo "task" > "$tmp/brief.md"
env_run() { HOME="$tmp" PATH="$tmp/bin:/usr/bin:/bin" ZSTACK_ENSURED=1 ONIGIRI_WAKE_DRY=1 ONIGIRI_ROOT="$tmp/root" "$@"; }

for rc in 0 7; do
  want=$([ $rc = 0 ] && echo pi-done || echo pi-failed)
  echo $rc > "$tmp/rc"; rm -f "$tmp/seen-wake"
  out=$(env_run "$src/hosts/vm/pi-run" vm-s$rc coding "$tmp/brief.md" 2>"$tmp/err"); got=$?
  check "vm: launcher keeps pi's rc $rc" "$got" $rc
  check "vm: one $want event for vm-s$rc" "$(printf '%s' "$out" | /usr/bin/python3 -c 'import json,sys; b=[json.loads(l) for l in sys.stdin]; print(len(b), b[0]["kind"], b[0]["id"], b[0]["rc"])')" "1 $want vm-s$rc $rc"
  has "vm: status line on stderr" "$(cat "$tmp/err")" "wake: $want not sent: dry run"
  check "vm: pi saw ONIGIRI_WAKE" "$(cat "$tmp/seen-wake")" "$src/hosts/vm/wake.py"

  rm -f "$tmp/seen-wake"
  env_run "$src/hosts/mini/pi-run-mini" mini-s$rc "$tmp/brief.md" > /dev/null 2>&1
  d="$tmp/onigiri/review/mini-s$rc"
  for _ in $(seq 50); do [ -e "$d/run.pid" ] || break; sleep 0.1; done   # bounded: 5s
  check "mini: run.rc is pi's rc $rc" "$(cat "$d/run.rc" 2>/dev/null)" $rc
  check "mini: run.pid removed after the wake" "$([ -e "$d/run.pid" ] && echo left || echo gone)" gone
  has "mini: $want event for pi-mini-s$rc in run.log" "$(cat "$d/run.log")" "\"kind\": \"$want\", \"id\": \"pi-mini-s$rc\""
  has "mini: status line in run.log" "$(cat "$d/run.log")" "wake: $want not sent: dry run"
  check "mini: pi saw ONIGIRI_WAKE" "$(cat "$tmp/seen-wake" 2>/dev/null)" "$src/hosts/vm/wake.py"
done

# without a key and without dry mode the launcher still exits with pi's rc and logs that it skipped.
echo 0 > "$tmp/rc"
HOME="$tmp" PATH="$tmp/bin:/usr/bin:/bin" ZSTACK_ENSURED=1 ONIGIRI_ROOT="$tmp/root" \
  "$src/hosts/vm/pi-run" vm-nokey coding "$tmp/brief.md" > /dev/null 2>"$tmp/err"; got=$?
check "vm: no key still exits 0" "$got" 0
has "vm: no key says so" "$(cat "$tmp/err")" "wake: pi-done webhook not configured"
# TERM to the wrapper (the pid in the pidfile) reaches pi, and the wake still fires with pi's rc.
touch "$tmp/hang"; rm -f "$tmp/seen-wake"
env_run "$src/hosts/vm/pi-run" vm-term coding "$tmp/brief.md" > "$tmp/out" 2>&1 & w=$!
for _ in $(seq 50); do [ -e "$tmp/seen-wake" ] && break; sleep 0.1; done   # bounded: 5s
kill -TERM "$(cut -d' ' -f1 "$tmp/root/run/vm-term.pid")"; wait $w; got=$?
check "vm: TERM to the wrapper exits with pi's rc" "$got" 143
check "vm: pi got the TERM" "$(cat "$tmp/got-term" 2>/dev/null)" term
has "vm: TERM still fires pi-failed" "$(cat "$tmp/out")" '"kind": "pi-failed"'
rm "$tmp/hang"
exit $fail

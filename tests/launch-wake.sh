#!/bin/bash
# Runs both launchers against a fake `pi` and a temp HOME, with ONIGIRI_WAKE_DRY=1 so no POST leaves the machine.
# Checks each one fires one pi-failed event when pi fails, none when it exits 0, and keeps pi's exit code.
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
  *) /usr/bin/python3 -c 'import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)' > "$tmp/int-ignored"
     trap 'echo TERM > "$tmp/got-sig"; exit 143' TERM; trap 'echo HUP > "$tmp/got-sig"; exit 129' HUP
     echo "\$ONIGIRI_WAKE" > "$tmp/seen-wake"   # last: the signal checks wait for it, so the traps are set by then
     [ -e "$tmp/hang" ] && while :; do sleep 0.1; done
     exit \$(cat "$tmp/rc");;
esac
EOF
chmod +x "$tmp/bin/pi"
: > "$tmp/onigiri/env.sh"; : > "$tmp/.cargo/env"
printf '#!/bin/sh\n' > "$tmp/onigiri/bin/sync-from-vm.sh"; chmod +x "$tmp/onigiri/bin/sync-from-vm.sh"
echo "task" > "$tmp/brief.md"
env_run() { HOME="$tmp" PATH="$tmp/bin:/usr/bin:/bin" ZSTACK_ENSURED=1 ONIGIRI_WAKE_DRY=1 ONIGIRI_ROOT="$tmp/root" "$@"; }

for rc in 0 7; do
  if [ $rc = 0 ]; then want=pi-done status="not sent: only failures wake" n=0; else want=pi-failed status="not sent: dry run" n=1; fi
  echo $rc > "$tmp/rc"; rm -f "$tmp/seen-wake"
  out=$(env_run "$src/hosts/vm/pi-run" vm-s$rc coding "$tmp/brief.md" 2>"$tmp/err"); got=$?
  check "vm: launcher keeps pi's rc $rc" "$got" $rc
  check "vm: $n $want event for vm-s$rc" "$(printf '%s' "$out" | /usr/bin/python3 -c 'import json,sys; b=[json.loads(l) for l in sys.stdin]; print(len(b), *(b[0]["kind"], b[0]["id"], b[0]["rc"]) if b else ())')" "$([ $n = 1 ] && echo "1 $want vm-s$rc $rc" || echo 0)"
  has "vm: status line on stderr" "$(cat "$tmp/err")" "wake: $want $status"
  check "vm: pi saw ONIGIRI_WAKE" "$(cat "$tmp/seen-wake")" "$src/hosts/vm/wake.py"

  rm -f "$tmp/seen-wake"
  env_run "$src/hosts/mini/pi-run-mini" mini-s$rc "$tmp/brief.md" > /dev/null 2>&1
  d="$tmp/onigiri/review/mini-s$rc"
  for _ in $(seq 50); do [ -e "$d/run.pid" ] || break; sleep 0.1; done   # bounded: 5s
  check "mini: run.rc is pi's rc $rc" "$(cat "$d/run.rc" 2>/dev/null)" $rc
  check "mini: run.pid removed after the wake" "$([ -e "$d/run.pid" ] && echo left || echo gone)" gone
  check "mini: $n $want event for pi-mini-s$rc in run.log" "$(grep -c "\"kind\": \"$want\", \"id\": \"pi-mini-s$rc\"" "$d/run.log")" $n
  has "mini: status line in run.log" "$(cat "$d/run.log")" "wake: $want $status"
  check "mini: pi saw ONIGIRI_WAKE" "$(cat "$tmp/seen-wake" 2>/dev/null)" "$src/hosts/vm/wake.py"
done

# without a key and without dry mode the launcher still exits with pi's rc and logs that it skipped.
echo 5 > "$tmp/rc"
HOME="$tmp" PATH="$tmp/bin:/usr/bin:/bin" ZSTACK_ENSURED=1 ONIGIRI_ROOT="$tmp/root" \
  "$src/hosts/vm/pi-run" vm-nokey coding "$tmp/brief.md" > /dev/null 2>"$tmp/err"; got=$?
check "vm: no key still exits with pi's rc" "$got" 5
has "vm: no key says so" "$(cat "$tmp/err")" "wake: pi-failed webhook not configured"
# a signal to the wrapper (the pid in the pidfile) reaches pi as the same signal, and the wake still fires with pi's rc.
touch "$tmp/hang"
for sig in TERM:143 HUP:129; do
  name=${sig%:*}; want=${sig#*:}; rm -f "$tmp/seen-wake" "$tmp/got-sig"
  env_run "$src/hosts/vm/pi-run" "vm-$name" coding "$tmp/brief.md" > "$tmp/out" 2>&1 & w=$!
  for _ in $(seq 50); do [ -e "$tmp/seen-wake" ] && break; sleep 0.1; done   # bounded: 5s
  kill -"$name" "$(cut -d' ' -f1 "$tmp/root/run/vm-$name.pid")"; wait $w; got=$?
  check "vm: $name to the wrapper exits with pi's rc" "$got" "$want"
  check "vm: pi got $name" "$(cat "$tmp/got-sig" 2>/dev/null)" "$name"
  has "vm: $name still fires pi-failed" "$(cat "$tmp/out")" '"kind": "pi-failed"'
done
rm "$tmp/hang"

# pi keeps a working Ctrl-C though it now runs as a background job, which bash starts with INT ignored.
# The subshell's trap - INT restores it on bash 4 and later (the VM and CI); macOS /bin/bash 3.2 does not.
# LAUNCH_BASH picks the bash that runs the launcher (default its #!/bin/bash).
lb=${LAUNCH_BASH:-/bin/bash}
if [ "$("$lb" -c "echo \${BASH_VERSINFO[0]}")" -ge 4 ]; then
  env_run "$lb" "$src/hosts/vm/pi-run" vm-int coding "$tmp/brief.md" > /dev/null 2>&1
  check "vm: pi starts with INT not ignored" "$(cat "$tmp/int-ignored")" False
else echo "skip  vm: INT disposition needs bash 4+ ($lb is older; set LAUNCH_BASH)"; fi
exit $fail

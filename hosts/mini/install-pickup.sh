#!/bin/bash
# install-pickup.sh [on|off|status]: the mini's schedule for bin/pickup, a launchd LaunchAgent that starts one pass every
# config/pickup.json interval_s seconds (StartInterval). It is a plain timer: no KeepAlive and no RunAtLoad, so launchd
# never restarts a pass that ended, and a pass that is still running when the timer fires is not started again (launchd
# coalesces it; bin/pickup's lock covers a hand run too). `on` writes the plist and loads it, again after a change of
# interval_s; `off` unloads it and removes the plist; `status` prints the plist's interval and launchd's state.
# The job runs the package's bin/pickup with ONIGIRI_HOST=mini, so hosts/mini/host.sh gives it the launch env.
set -eu
pkg="$(cd "$(dirname "$0")/../.." && pwd)"
label=com.onigiri.pickup
plist="$HOME/Library/LaunchAgents/$label.plist"
log="${ONIGIRI_ROOT:-$HOME/onigiri}/pickup/launchd.log"
domain="gui/$(id -u)"
case "${1:-status}" in
  on)
    interval=$(python3 -I -c 'import json, sys; print(int(json.load(open(sys.argv[1]))["interval_s"]))' "$pkg/config/pickup.json")
    mkdir -p "$(dirname "$plist")" "$(dirname "$log")"
    cat > "$plist.tmp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$pkg/bin/pickup</string></array>
  <key>EnvironmentVariables</key><dict><key>ONIGIRI_HOST</key><string>mini</string><key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartInterval</key><integer>$interval</integer>
  <key>AbandonProcessGroup</key><true/>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>$log</string>
  <key>StandardErrorPath</key><string>$log</string>
</dict></plist>
PLIST
    plutil -lint "$plist.tmp" >/dev/null
    mv "$plist.tmp" "$plist"
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$plist"
    echo "pickup: on, every ${interval}s ($plist)";;
  off)
    launchctl bootout "$domain/$label" 2>/dev/null || true
    rm -f "$plist"
    echo "pickup: off";;
  status)
    if [ -f "$plist" ]; then
      echo "pickup: plist every $(plutil -extract StartInterval raw "$plist")s"
      launchctl print "$domain/$label" 2>/dev/null | grep -E '^\s+(state|runs|last exit code|run interval) =' || echo "pickup: not loaded"
    else
      echo "pickup: off (no $plist)"
    fi;;
  *) echo "usage: install-pickup.sh [on|off|status]" >&2; exit 2;;
esac

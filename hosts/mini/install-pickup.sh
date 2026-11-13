#!/bin/bash
set -eu
pkg="$(cd "$(dirname "$0")/../.." && pwd)"
label=com.onigiri.pickup
plist="$HOME/Library/LaunchAgents/$label.plist"
log="${ONIGIRI_ROOT:-$HOME/onigiri}/pickup/launchd.log"
domain="gui/$(id -u)"
render() {
  interval=$("$pkg/bin/policy" get pickup.interval_s)
  cat <<PLIST
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
}
case "${1:-status}" in
  on)
    mkdir -p "$(dirname "$plist")" "$(dirname "$log")"
    render > "$plist.tmp"
    plutil -lint "$plist.tmp" >/dev/null
    mv "$plist.tmp" "$plist"
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$plist"
    echo "pickup: on, every $(plutil -extract StartInterval raw "$plist")s ($plist)";;
  print) render;;
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
  *) echo "usage: install-pickup.sh [on|off|status|print]" >&2; exit 2;;
esac

#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
[ "$(uname -s)" = Darwin ] || { echo "skip  vm-launchd: macOS only"; exit 0; }
py=${VM_TEST_PYTHON:-$(command -v python3)}
label=com.onigiri.vm-launchd-test.$$
tmp=$(cd "$(mktemp -d)" && pwd -P)
trap 'launchctl bootout "gui/$(id -u)/$label" 2>/dev/null; rm -rf "$tmp"' EXIT
cat > "$tmp/job.sh" <<EOF
#!/bin/bash
"$py" -c 'import subprocess, sys
p = subprocess.run([sys.argv[1], "echo ok; exit 7"], capture_output=True, text=True)
print(p.returncode, p.stdout.strip())' "$src/hosts/mini/vm" > "$tmp/fifo" 2>&1
EOF
chmod +x "$tmp/job.sh"
cat > "$tmp/job.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array><string>$tmp/job.sh</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOF
mkfifo "$tmp/fifo"
launchctl bootstrap "gui/$(id -u)" "$tmp/job.plist" || exit 1
got=$(perl -e 'alarm 60; exec @ARGV' cat "$tmp/fifo")
if [ "$got" = "7 ok" ]; then echo "ok    hosts/mini/vm from a launchd job under $py"; exit 0; fi
echo "FAIL  hosts/mini/vm from a launchd job under $py: got '$got', want '7 ok'"; exit 1

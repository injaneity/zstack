#!/bin/bash
# Copies zstack's subagent types into pi-subagent-manager's user folder.
# An existing file is left alone, so models set there survive a reinstall.
# usage: bin/install-agents.sh [--force]
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)/agents"
dest="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/subagent-manager/agents"
mkdir -p "$dest"
for f in "$src"/zstack-*.md; do
  name=$(basename "$f")
  if [ -e "$dest/$name" ] && [ "${1:-}" != "--force" ]; then
    echo "kept    $dest/$name"
  else
    cp "$f" "$dest/$name"
    echo "copied  $dest/$name"
  fi
done

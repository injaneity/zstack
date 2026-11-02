#!/bin/bash
# Copies zstack's agent files into ~/.pi/agent/agents/, where the subagent tool looks.
# A package can't point the loader at its own agents/ directory, so this copy is the install.
# An existing file is left alone, so a model set there survives a reinstall.
# usage: bin/install-agents.sh [--force]
set -euo pipefail
src="$(cd "$(dirname "$0")/.." && pwd)/agents"
dest="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/agents"
mkdir -p "$dest"
for f in "$src"/*.md; do
  name=$(basename "$f")
  if [ -e "$dest/$name" ] && [ "${1:-}" != "--force" ]; then
    echo "kept    $dest/$name"
  else
    cp "$f" "$dest/$name"
    echo "copied  $dest/$name"
  fi
done

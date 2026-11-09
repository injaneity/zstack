#!/bin/bash
# One-time setup after `pi install git:github.com/injaneity/zstack`. Safe to re-run: it never
# overwrites a file you have, and asks before touching gh.
# usage: bin/setup.sh [<repo dir you will work in>]
set -eu
pkg="$(cd "$(dirname "$0")/.." && pwd)"
agent_dir="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
changed=() kept=()

command -v pi >/dev/null || { echo "pi is not on PATH. Install pi first, then re-run this." >&2; exit 1; }
echo "pi $(pi --version)"

while read -r state file; do
  if [ "$state" = copied ]; then changed+=("agent   $file"); else kept+=("agent   $file (already there)"); fi
done < <(bash "$pkg/bin/install-agents.sh")

# gh is needed only to open or merge PRs on GitHub, so it is checked only for a GitHub repo.
repo="${1:-$PWD}"
if git -C "$repo" remote -v 2>/dev/null | grep -q github.com; then
  if ! command -v gh >/dev/null; then kept+=("gh      not installed; PR steps will fail. Install it from https://cli.github.com")
  elif gh auth status >/dev/null 2>&1; then kept+=("gh      logged in")
  elif [ -t 0 ]; then
    printf 'gh is not logged in, and %s is a GitHub repo. Run `gh auth login` now? [y/N] ' "$repo"
    read -r answer || answer=
    if [ "$answer" = y ] || [ "$answer" = Y ]; then gh auth login && changed+=("gh      logged in"); else kept+=("gh      not logged in (you said no)"); fi
  else kept+=("gh      not logged in; run: gh auth login"); fi
else kept+=("gh      not checked ($repo is not a GitHub repo)"); fi

echo "changed:"; [ ${#changed[@]} -gt 0 ] && printf '  %s\n' "${changed[@]}" || echo "  nothing"
echo "not changed:"; [ ${#kept[@]} -gt 0 ] && printf '  %s\n' "${kept[@]}" || echo "  nothing"
echo "pi uses its default model unless you pass one. Set it with /model in pi, or defaultProvider and defaultModel in $agent_dir/settings.json."

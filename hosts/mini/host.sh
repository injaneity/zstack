# shellcheck shell=bash disable=SC1091
# Sourced by bin/pi-run when ONIGIRI_HOST=mini, with the subcommand (start, dry, collect, list) as $1.
source "$HOME/onigiri/env.sh"; . "$HOME/.cargo/env"
export PATH="$HOME/onigiri/mamba/envs/bash/bin:$PATH" CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-3}
export ONIGIRI_ROOT="${ONIGIRI_ROOT:-$HOME/onigiri}" ONIGIRI_BRIEF_PREFIX="${BASH_SOURCE[0]%/*}/host-mini.md"
if [ "$1" = start ]; then
  "$HOME/onigiri/bin/sync-from-vm.sh" || echo "host: sync failed, using copies from $(cat "$HOME/onigiri/sync/synced-at")"
fi
# A launch on a host that is not merged main and nothing else starts nothing: a failing check fails this file.
case "$1" in start|dry) "${BASH_SOURCE[0]%/*}/../check-parity";; esac

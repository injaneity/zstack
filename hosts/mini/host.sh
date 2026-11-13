# shellcheck shell=bash disable=SC1091
source "$HOME/onigiri/env.sh"; . "$HOME/.cargo/env"
export PATH="$HOME/onigiri/mamba/envs/py/bin:$HOME/onigiri/mamba/envs/bash/bin:$PATH" CARGO_INCREMENTAL=0 CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-3}
export ONIGIRI_ROOT="${ONIGIRI_ROOT:-$HOME/onigiri}" ONIGIRI_BRIEF_PREFIX="${BASH_SOURCE[0]%/*}/host-mini.md"
export ONIGIRI_PEERS="${ONIGIRI_PEERS-vm=${BASH_SOURCE[0]%/*}/vm}"
export ONIGIRI_MERGE_HOST="${ONIGIRI_MERGE_HOST-vm}"
if [ "$1" = start ]; then
  "${BASH_SOURCE[0]%/*}/sync-from-vm.sh" || echo "host: sync failed, using copies from $(cat "$HOME/onigiri/sync/synced-at")"
fi
case "$1" in start|dry) "${BASH_SOURCE[0]%/*}/../check-parity";; esac

# shellcheck shell=bash disable=SC1091
if [ -r "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1; fi
export PATH="$HOME/.local/bin:$HOME/bin:$PATH" ONIGIRI_ROOT=${ONIGIRI_ROOT:-/srv/onigiri}
export TMPDIR=/var/tmp/onigiri; mkdir -p "$TMPDIR"
if command -v sccache >/dev/null 2>&1; then
  export RUSTC_WRAPPER=sccache SCCACHE_DIR="$HOME/.cache/sccache" SCCACHE_CACHE_SIZE=3G CARGO_INCREMENTAL=0
fi
export ONIGIRI_MERGE_HOST="${ONIGIRI_MERGE_HOST-vm}"
export ONIGIRI_UNSEEN="${ONIGIRI_UNSEEN-mini}"
case "$1" in start|dry) "${BASH_SOURCE[0]%/*}/../check-parity";; esac

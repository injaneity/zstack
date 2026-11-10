# shellcheck shell=bash disable=SC1091
# Sourced by bin/pi-run when ONIGIRI_HOST or ~/.config/onigiri/host names vm, with the subcommand (start, dry, collect, list) as $1.
# The Claude token is in ~/.profile; a shell without it looks logged out.
if [ -r "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1; fi
export PATH="$HOME/.local/bin:$HOME/bin:$PATH" ONIGIRI_ROOT=${ONIGIRI_ROOT:-/srv/onigiri}
# /tmp is a small tmpfs in the VM; scratch goes to disk.
export TMPDIR=/var/tmp/onigiri; mkdir -p "$TMPDIR"
# One shared Rust compile cache for every session (Zane, 4 Nov); each session keeps its own target dir.
if command -v sccache >/dev/null 2>&1; then
  export RUSTC_WRAPPER=sccache SCCACHE_DIR="$HOME/.cache/sccache" SCCACHE_CACHE_SIZE=3G CARGO_INCREMENTAL=0
fi
export ONIGIRI_MERGE_HOST="${ONIGIRI_MERGE_HOST-vm}"   # hosts/vm/merge_job.py runs on the VM (docs/HOST.md)
# A launch on a host that is not merged main and nothing else starts nothing: a failing check fails this file.
case "$1" in start|dry) "${BASH_SOURCE[0]%/*}/../check-parity";; esac

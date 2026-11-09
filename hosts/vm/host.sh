# shellcheck shell=bash disable=SC1091
# Sourced by bin/pi-run when ONIGIRI_HOST=vm, with the subcommand (start, dry, collect, list) as $1.
# The Claude token is in ~/.profile; a shell without it looks logged out.
if [ -r "$HOME/.profile" ]; then . "$HOME/.profile" >/dev/null 2>&1; fi
export PATH="$HOME/.local/bin:$HOME/bin:$PATH" ONIGIRI_ROOT=${ONIGIRI_ROOT:-/srv/onigiri}
# A launch on a host that is not merged main and nothing else starts nothing: a failing check fails this file.
case "$1" in start|dry) "${BASH_SOURCE[0]%/*}/../check-parity";; esac

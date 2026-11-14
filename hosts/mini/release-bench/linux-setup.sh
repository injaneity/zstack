#!/usr/bin/env bash
set -eu
R=${RB_DIR:?RB_DIR: the run dir on the VM}; W=$R/work; mkdir -p $W $R/logs $R/shim
ln -sf /usr/bin/chromium $R/shim/google-chrome
ln -sf $HOME/.nvm/versions/node/v22.23.3/bin/npx $R/shim/npx
export PATH=$HOME/.nvm/versions/node/v22.23.3/bin:$HOME/.local/bin:$PATH
if [ ! -d $W/taskpack ]; then
  rm -rf $W/taskpack.new; cp -a $R/wt/libs/cua-driver-bench/taskpack $W/taskpack.new
  for app in $W/taskpack.new/tasks/shared/*/apps/*; do
    (cd "$app" && npm ci --no-audit --no-fund --silent && { [ -d node_modules/electron/dist ] || node node_modules/electron/install.js; })
  done
  mv $W/taskpack.new $W/taskpack
fi
cd $R/wt/libs/cua-driver-bench/automated-eval/macos_bench && CDB_TASKPACK=$W/taskpack python3 tools/check_pack_pins.py 2>&1 | tail -3
for v in $(ls -d $R/drv-* | sed 's/.*drv-//'); do echo "$v $($R/drv-$v/cua-driver --version) $(sha256sum $R/drv-$v/cua-driver | cut -c1-16)"; done

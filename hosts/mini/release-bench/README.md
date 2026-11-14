# Release bench scripts (the mini and its guests)

The host side of `skills/onigiri-mode/playbooks/release-bench.md`, as the first release bench ran it (cua-driver 0.49.11
against 0.49.4, 14 Nov). Arm `pi-cua-driver-main`, `openai-codex/gpt-6.1-terra` through pi, Bench v2.

- `serial.sh RUN_DIR VM_RUN_DIR OLD NEW` runs the macOS lane, then the Linux lane, one trial at a time on the mini.
- macOS: `guest/` goes to the Lume bench guest's `~/rb/guest/`, with the releases' darwin-arm64 `cua-driver`,
  `cua-cursor-theme`, `skills/cua-driver/` and `GIT_SHA` (the tag commit) in `~/rb/drv-<version>/`. `guest/run-all.sh`
  installs each release into the bench's private apps with `~/rb/guest/install-driver.sh` (the driver-latency-macos
  installer, which needs the guest's `~/bench-work` from the bench seed) and runs `guest/session.sh` per task.
  `ssh.sh` reaches the guest at `$RB_DIR/guest-ip`.
- Linux: on the VM, `VM_RUN_DIR` holds `wt` (a trycua/cua worktree at origin/main) and `drv-<version>/` (the
  linux-arm64-binary `cua-driver`, `skills/`, `GIT_SHA`). `linux-setup.sh` builds the taskpack; `linux-all.sh` runs
  `linux-session.sh` per task and release. Release binaries report an empty `git_sha`, so `release_pins.py` pins that.
- `skills/onigiri-mode/scripts/release-compare.py OLD NEW DIR...` scores the rows with the decision rule.

## Usage

| Script | Arguments | What it does |
| --- | --- | --- |
| `serial.sh` | `RUN_DIR VM_RUN_DIR OLD NEW` | macOS lane, then Linux lane. `P1` sets runs per task and release (default 5). |
| `ssh.sh` | a command | ssh to the guest at `RB_IP`, else `$RB_DIR/guest-ip`, as `lume`. |
| `guest/run-all.sh` | `OLD NEW` | macOS: foreground (10 tasks), then background (8; MB-10 and MB-11 are foreground only). Per task, both releases back to back, and the release that goes first alternates by task. Run ids are `rb-m-<f\|b>g-<version>-<task>`. A DONE run is skipped, so a rerun resumes. Stops when disk falls under 15G. |
| `guest/session.sh` | `DRV_DIR TASK CONDITION RUN_ID` | One task × `P1` runs on the installed release. `PREFLIGHT=1` adds the runner's preflight. |
| `guest/install-driver.sh` | `DRV_DIR` | Signs the release into the bench's private main and script apps, re-grants TCC and writes `DRV_DIR/pins.json`. |
| `linux-setup.sh` | (env `RB_DIR`) | Shims, logs and the taskpack. Checks pack pins and each `drv-<v>`. |
| `linux-all.sh` | `OLD NEW` (env `RB_DIR`) | Linux foreground over all 10 tasks, ordered as on macOS. Run ids are `rb-l-<version>-<task>`. Writes a STOP under 20G free. Rerun resumes. |
| `linux-session.sh` | `VERSION TASK RUN_ID` (env `RB_DIR`) | One task × `P1` runs under bwrap with `launch/linux_session.sh`. |
| `release_pins.py` | `BIN SKILLS DOCTOR_JSON OUT TAG_SHA` | `make_linux_pins` for a release binary. Writes `release.json` with the tag commit. |

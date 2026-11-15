#!/bin/zsh
set -e
D=${1:A}; W=~/bench-work; MB=$W/src/macos_bench
M=$W/cua-main; S=$W/cua-script; OLD=$W/cua-0.34.0/CuaDriver-0.34.0.app
VERSION=$($D/cua-driver --version | awk '{print $2}')
SHA=$(cat $D/GIT_SHA)
build_app() {
  local A=$1 ID=$2 NAME=$3
  rm -rf $A; mkdir -p $A/Contents/MacOS $A/Contents/Resources
  cp $D/cua-driver $D/cua-cursor-theme $A/Contents/MacOS/
  cp $OLD/Contents/Resources/AppIcon.icns $A/Contents/Resources/
  cp $OLD/Contents/Info.plist $A/Contents/Info.plist
  plutil -replace CFBundleIdentifier -string $ID $A/Contents/Info.plist
  plutil -replace CFBundleName -string "$NAME" $A/Contents/Info.plist
  plutil -replace CFBundleDisplayName -string "$NAME" $A/Contents/Info.plist
  plutil -replace CFBundleShortVersionString -string "$VERSION" $A/Contents/Info.plist
  plutil -replace CFBundleVersion -string "${VERSION}.${SHA:0:9}" $A/Contents/Info.plist
  codesign --force --sign - --identifier $ID --options runtime --entitlements $M/ent.plist $A/Contents/MacOS/cua-cursor-theme
  codesign --force --sign - --identifier $ID --options runtime --entitlements $M/ent.plist $A
  codesign --verify --strict $A
  zsh $MB/tools/vm_main_build/tcc_grant.sh $A $ID > /dev/null
}
pkill -f "$M/CuaDriverBenchMain.app/Contents/MacOS/cua-driver" || true
pkill -f "$S/CuaDriverBenchScript.app/Contents/MacOS/cua-driver" || true
build_app $M/CuaDriverBenchMain.app com.trycua.driver.benchmain "Cua Driver Bench Main"
build_app $S/CuaDriverBenchScript.app com.trycua.driver.benchscript "Cua Driver Bench Script"
rm -rf $M/skills; mkdir -p $M/skills; cp -R $D/skills/cua-driver $M/skills/cua-driver
/opt/homebrew/bin/python3 - "$MB/pins.json" "$D/pins.json" "$VERSION" "$SHA" \
  "$(shasum -a 256 $M/CuaDriverBenchMain.app/Contents/MacOS/cua-driver | cut -c1-64)" \
  "$(shasum -a 256 $S/CuaDriverBenchScript.app/Contents/MacOS/cua-driver | cut -c1-64)" "$MB" "$M/skills/cua-driver" <<'PY'
import json, sys
src, dst, ver, sha, b, sb, mb, skills = sys.argv[1:]
sys.path.insert(0, mb)
import claude_arms as ca
from pathlib import Path
p = json.load(open(src))
p["cua_main"] = {"note": "driver-latency-macos bench build", "git_sha": sha, "version": ver,
                 "version_string": f"cua-driver {ver}", "binary_sha256": b, "script_binary_sha256": sb,
                 "skills_tree_sha256": ca.sha256_tree(Path(skills))}
json.dump(p, open(dst, "w"), indent=1)
print(json.dumps(p["cua_main"]))
PY
APPR="Library/Group Containers/group.com.apple.replayd/ScreenCaptureApprovals.plist"
for H in $M/daemon-state/home $S/daemon-state/home; do mkdir -p "$H/${APPR:h}"; cp ~/"$APPR" "$H/$APPR"; done
echo INSTALLED $SHA

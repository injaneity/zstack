#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tools")); sys.path.insert(0, str(Path.cwd()))
import make_linux_pins as m
binary, skills, doctor, out, tag_sha = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), sys.argv[5]
report = json.loads(doctor.read_text())
build = report.setdefault("build", {})
if build.get("git_sha"):
    raise SystemExit("this binary reports a git_sha: use tools/make_linux_pins.py")
build["git_sha"] = "-"
version = build.get("version")
pins = m.build_pins(binary, skills, report, version)
pins["cua_driver_git_sha"] = ""
pins["cua_main"]["git_sha"] = ""
pins["cua_main"]["note"] = f"published release {version}, tag commit {tag_sha} (release builds report no git_sha)"
out.write_text(json.dumps(pins, indent=1))
(out.parent / "release.json").write_text(json.dumps({"version": version, "tag_commit": tag_sha}))

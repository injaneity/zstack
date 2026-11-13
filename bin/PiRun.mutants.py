import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    ("paused-holds-slot", "a 429'd run keeps its slot (bin/hourly slots() counted paused as running before)",
     [("/\\ state' = \"queued\" /\\ cont' = TRUE /\\ live' = FALSE", "/\\ state' = \"queued\" /\\ cont' = TRUE /\\ live' = live")], "PauseIsSilent"),
    ("resume-skips-queue", "a direct start resumes a 429'd run (pi-run resume <id> before)",
     [("  /\\ state \\in {\"absent\", \"queued\"} /\\ ~cont\n", "  /\\ state \\in {\"absent\", \"queued\"}\n"),
      ("/\\ state' = \"running\" /\\ live' = TRUE /\\ reopened' = FALSE\n  /\\ UNCHANGED <<cont, ",
       "/\\ state' = \"running\" /\\ live' = TRUE /\\ reopened' = cont /\\ cont' = FALSE\n  /\\ UNCHANGED <<")], "ResumeGoesThroughTheQueue"),
    ("lost-run-sends-no-wake", "a lost run's collect sends nothing (bin/pi-run collect before)",
     [("wake' = IF Lost THEN \"failed\" ELSE wake", "wake' = wake")], "LostWakesAtCollect"),
    ("retry-reuses-id", "a start from collected reopens the same session (step() before)",
     [("  /\\ state \\in {\"absent\", \"queued\"} /\\ ~cont\n",
       "  /\\ state \\in {\"absent\", \"queued\", \"collected\"} /\\ ~cont\n")], "CollectedIsFinal"),
    ("continuation-never-drained", "the drain never resumes a 429'd run",
     [("/\\ WF_vars(Resume) ", "")], "a temporal property"),
    ("pause-wakes", "a 429 sends a failed wake",
     [("/\\ why' = \"limit\" /\\ limits' = limits + 1\n  /\\ UNCHANGED <<ended, killed, collects, finish, wake, reopened, by, fixes, spent>>",
       "/\\ why' = \"limit\" /\\ limits' = limits + 1 /\\ wake' = \"failed\"\n"
       "  /\\ UNCHANGED <<ended, killed, collects, finish, reopened, by, fixes, spent>>")], "PauseIsSilent"),
    ("resume-uncapped", "an upstream limit resumes a run on every drain, past config/policy.toml resume.cap (before the cap)",
     [("/\\ state = \"running\" /\\ live /\\ limits < ResumeCap", "/\\ state = \"running\" /\\ live /\\ limits <= ResumeCap")],
     "ResumesCapped"),
    ("spent-limit-silent", "a run out of resumes ends with no failed wake",
     [("/\\ ended' = TRUE /\\ wake' = \"failed\" /\\ spent' = TRUE", "/\\ ended' = TRUE /\\ wake' = \"none\" /\\ spent' = TRUE")],
     "SpentLimitFails"),
    ("ci-fix-uncapped", "a red PR reopens its thread past config/policy.toml ci.fix_attempts",
     [("/\\ state = \"collected\" /\\ fixes < FixAttempts", "/\\ state = \"collected\" /\\ fixes < FixAttempts + 1")], "FixesCapped"),
    ("ci-reopen-before-collect", "a red PR reopens a thread whose result the pickup has not taken",
     [("/\\ state = \"collected\" /\\ fixes < FixAttempts", "/\\ state \\in {\"collected\", \"exited\"} /\\ fixes < FixAttempts")],
     "CiReopenAfterCollect"),
]


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "PiRun.tla")).read()
    cfg = open(os.path.join(HERE, "PiRun.cfg")).read()
    bad = 0
    for name, why, edits, rule in M:
        s = src
        for old, new in edits:
            if s.count(old) != 1:
                print(f"BROKEN   {name}: edit not found once: {old[:60]!r}")
                bad = 1
                break
            s = s.replace(old, new)
        else:
            d = os.path.join(scratch, name)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "PiRun.tla"), "w").write(s)
            open(os.path.join(d, "PiRun.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-cp", jar, "tlc2.TLC", "-workers", "1", "-metadir", os.path.join(d, "meta"),
                                  "-config", "PiRun.cfg", "PiRun.tla"], cwd=d, capture_output=True, text=True, timeout=600).stdout
            m = re.search(r"Invariant (\w+) is violated|(Temporal properties were violated)|(Action property \w+ is violated)"
                          r"|(not completely specified|Deadlock)", out)
            got = m and (m.group(1) or (m.group(3) and m.group(3).split()[2]) or (m.group(2) and "a temporal property"))
            if got == rule:
                print(f"killed   {name}: {why} -> {got} breaks")
            else:
                print(f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(4)) or 'no error'} (expected {rule})")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))

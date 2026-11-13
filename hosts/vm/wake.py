#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

URL = "https://api2.cursor.sh/automations/webhook/788bf148-65fb-5299-ae09-83cd6ae301a5"
KEY_FILE = os.path.expanduser("~/.config/onigiri-urgent-webhook")
SGT = ZoneInfo("Asia/Singapore")
KINDS = ("blocked", "failed", "done")
FIELDS = {"pr": r"\d+", "rc": r"\d+", "report": r".+", "prs": r"\S+"}


def now():
    return datetime.now(SGT).isoformat(timespec="seconds")


def send(body, attempts=3, timeout=5):
    try:
        with open(KEY_FILE) as f:
            key = f.read().strip()
    except OSError:
        key = ""
    if not key:
        return "webhook not configured"
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    for n in range(attempts):
        if n:
            time.sleep(n)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return f"webhook sent ({r.status})"
        except urllib.error.HTTPError as e:
            status = f"webhook failed: HTTP {e.code}"
            if e.code < 500:
                break
        except Exception as e:
            status = f"webhook failed: {type(e).__name__}"
    return status


def sha():
    here = os.path.dirname(os.path.realpath(__file__))
    r = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    return r.stdout.strip() or "unknown"


def post(kind, message, fields=None):
    body = {"kind": kind, "id": os.environ.get("PI_SESSION_ID") or None, "pr": None, "reason": message[:1000],
            "time": now(), "source": "pi", "host": socket.gethostname().split(".")[0]}
    body.update({k: int(v) if k in ("pr", "rc") else v.split(",") if k == "prs" else v for k, v in (fields or {}).items()})
    if os.environ.get("ONIGIRI_WAKE_DRY"):
        print(json.dumps(body))
        return "not sent: dry run"
    return send(body)


def main(argv):
    fields = dict(a.split("=", 1) for a in argv[2:] if "=" in a)
    if (len(argv) < 2 or argv[0] not in KINDS or not argv[1].strip() or len(fields) != len(argv) - 2
            or not all(k in FIELDS and re.fullmatch(FIELDS[k], v) for k, v in fields.items())):
        print("usage: wake.py blocked|failed|done <message> [pr=N] [rc=N] [report=<path>] [prs=<url,...>]", file=sys.stderr)
        return 2
    status = post(argv[0], argv[1], fields)
    print(f"zstack {sha()}\nwake: {argv[0]} {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

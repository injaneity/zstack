#!/usr/bin/env python3
"""wake.py: wake onigiri through the urgent-wake webhook the merge job already POSTs to. Best effort only.

  wake.py <kind> <message> [field=value ...]   a pi session mid-run: blocked, question, pr-opened, any kind
  wake.py --exit <rc> <session-id>             a launcher after pi exits: pi-done (rc 0) or pi-failed, with rc

<kind> is a lowercase slug the sender picks. The body keeps the merge job's keys (kind, id, pr, reason,
time) so the routine reads it the same way: <message> goes in reason, id defaults to $PI_SESSION_ID (pi sets it in
a session's shell), pr=N fills pr, and every other field rides alongside with source=pi and host. ONIGIRI_WAKE_DRY=1 prints the body and sends
nothing. Exits 0 whatever happens to the POST (2 on bad usage) and prints one log-safe status line on stderr.
The key is only in KEY_FILE; a host without it skips with "webhook not configured".
"""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# The urgent wake routine. The URL is not secret; the key is only in KEY_FILE (0600).
URL = "https://api2.cursor.sh/automations/webhook/788bf148-65fb-5299-ae09-83cd6ae301a5"
KEY_FILE = os.path.expanduser("~/.config/onigiri-urgent-webhook")
SGT = ZoneInfo("Asia/Singapore")
KIND = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FIELD = re.compile(r"([a-z][a-z0-9_]{0,31})=(.{0,500})", re.S)
RESERVED = {"kind", "reason", "time", "source"}


def now():
    return datetime.now(SGT).isoformat(timespec="seconds")


def send(body, attempts=3, timeout=5):
    """POST one event. Returns a log-safe status line; never echoes the key. Retries network errors and 5xx."""
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


def event(kind, message, fields):
    """The body for one pi event. fields are already parsed (name -> str)."""
    body = {"kind": kind, "id": os.environ.get("PI_SESSION_ID") or None, "pr": None, "reason": message,
            "time": now(), "source": "pi", "host": socket.gethostname().split(".")[0]}
    for k, v in fields.items():
        body[k] = int(v) if k == "pr" and v.isdigit() else v
    return body


def main(argv):
    usage = "usage: wake.py <kind> <message> [field=value ...] | wake.py --exit <rc> <session-id>"
    if len(argv) == 3 and argv[0] == "--exit" and argv[1].isdigit() and argv[2]:
        rc, sid = argv[1:]
        argv = ["pi-done" if rc == "0" else "pi-failed", f"{sid} exited rc={rc}", f"rc={rc}", f"id={sid}"]
    if len(argv) < 2 or not KIND.fullmatch(argv[0]) or not argv[1].strip():
        print(f"{usage}\n<kind> is a lowercase slug like blocked or pi-done; <message> is not empty", file=sys.stderr)
        return 2
    fields = {}
    for a in argv[2:]:
        m = FIELD.fullmatch(a)
        if not m or m.group(1) in RESERVED:
            print(f"{usage}\nbad field {a[:40]!r}: lowercase name=value, value up to 500 chars, not {sorted(RESERVED)}",
                  file=sys.stderr)
            return 2
        fields[m.group(1)] = m.group(2)
    body = event(argv[0], argv[1][:1000], fields)
    if os.environ.get("ONIGIRI_WAKE_DRY"):
        print(json.dumps(body))
        status = "not sent: dry run"
    else:
        status = send(body)
    print(f"wake: {argv[0]} {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

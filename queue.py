#!/usr/bin/env python3
import ctypes
import fcntl
import functools
import json
import locale
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone

PKG = os.path.dirname(os.path.realpath(__file__))
USAGE = (
    "The queue:\n"
    "  queue.py pass                    one pass of the machine: clear what has finished, free the slot of a thread\n"
    "                                   that died, and launch a thread for each ready item, up to the cap\n"
    "  queue.py add <id> [--priority N] [--preapproved]\n"
    "                                   a task from the orchestrator; its text on stdin\n"
    "  queue.py approve <id>            the owner preapproves an item already a task, running or waiting: its thread may\n"
    "                                   merge a passed head, and a pass merges its waiting PR once that head may merge, or\n"
    "                                   puts it in the base branch's merge queue when it has one. An item waiting because the\n"
    "                                   pre-push hook refused its push runs again\n"
    "  queue.py verified <id> <pr> <head> auto|review|fail\n"
    "                                   the verifier's verdict on that head: its thread may merge it, a person must\n"
    "                                   review it, or its thread fixes what was found\n"
    "  queue.py gate <pr> <head>        may that head of that PR merge now; exits 1 with the reason when it may not\n"
    "  queue.py release [<id>|--hook|--died]\n"
    "                                   the thread's turn ended: free its slot and tell the orchestrator the outcome.\n"
    "                                   An item whose PR is still open waits on it and keeps its worktree; a finished\n"
    "                                   one is forgotten. With no id, the item of the thread that runs it. With --hook,\n"
    "                                   exit 3 and free nothing while its open PR's head has no verdict. With --died,\n"
    "                                   the turn failed, so an item with no PR goes back to tasks to run again, as does\n"
    "                                   one whose turn ended with no PR and that was never marked complete. An item whose push\n"
    "                                   the pre-push hook refused, with no allowed remote pushable, waits for `approve` instead\n"
    "  queue.py complete <id>           the item's own thread made its deliverable, so its release with no PR clears it\n"
    "                                   as done instead of sending it back to tasks\n"
    "  queue.py list                    what this machine holds: running, waiting, tasks, verdicts, done\n"
    "The config:\n"
    "  queue.py check                   parse and validate the machine file, each repo it works on and the invariants\n"
    '  queue.py seat <role>[:<n>]       "<provider> <model>" for a role from [models]; a role with several seats needs :<n>\n'
    "  queue.py seats                   every role and seat\n"
    "  queue.py get <key>               one config value by dotted key (`merge.repo`): a string or number as is, else JSON\n"
    "  queue.py dump                    the merged config as JSON\n"
    "  queue.py invariants [<id>...]    the invariants as markdown, as a thread reads them; ids name single ones\n"
    "  queue.py warm                    in a new worktree of the clone: share what git ignores in the clone, copy-on-write,\n"
    "                                   and say what\n"
    "The machine:\n"
    "  queue.py update                  bring this checkout to origin/main, and the installed plugin with it\n"
    "  queue.py pre-push <remote> <url> the clone's pre-push hook: the repo's publishing invariants"
)
try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(PKG, "vendor"))
    import tomli as tomllib
CONFIG = os.path.abspath(os.environ.get("ZSTACK_CONFIG") or os.path.join(PKG, "config"))
INVARIANTS = os.path.join(PKG, "config", "invariants.toml")
THREAD_VAR = "T3CODE_THREAD_ID"
ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
SHA = r"[0-9a-f]{40}"
ENV = {**{k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"},
       "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or os.path.expanduser("~/.config/gh"),
       "PATH": os.pathsep.join([os.path.expanduser("~/.local/bin"), "/usr/local/bin", "/opt/homebrew/bin",
                                os.environ.get("PATH") or "/usr/bin:/bin"])}
NO_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Refused(Exception):
    pass


class Unfinished(Exception):
    pass


def text(v):
    return isinstance(v, str) and v.strip() != "" and v == v.strip()


def pos_int(v):
    return type(v) is int and v > 0


def seat(v):
    return isinstance(v, dict) and set(v) == {"provider", "model"} and all(isinstance(x, str) and x.strip() for x in v.values())


def names(v):
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v) and len(set(v)) == len(v)


def words(v):
    return names(v) and all(re.fullmatch(r"[a-z]+", x) for x in v)


def repo(v):
    return isinstance(v, str) and v.count("/") == 1 and all(v.split("/"))


def seats(v):
    return seat(v) or isinstance(v, list) and bool(v) and all(seat(x) for x in v)


def line_with(key):
    return (lambda v: text(v) and v.count(key) == 1 and "{" not in v.replace(key, ""), f"one line with {key} once and no other placeholder")


START_SECONDS = 120
SEAT = (seats, "a {provider, model} table or a non-empty list of them")
PATHISH = (lambda v: text(v) and v.startswith(("/", "~/")), "a path from / or ~/")
MACHINE = {
    "models": {role: SEAT for role in ("coding", "explorer", "designer", "verifier")},
    "run": {"idle_min": (pos_int, "a positive integer"), "threads": (pos_int, "a positive integer"),
            "t3": (text, "the T3 Code command: a name on PATH or a path"),
            "lost_min": (lambda v: pos_int(v) and v * 60 > START_SECONDS, f"whole minutes, more than the {START_SECONDS} seconds a start may take")},
    "wake": {"url": (lambda v: text(v) and v.startswith("https://"), "an https URL"), "key_file": PATHISH},
}
PLACE = {"clone": PATHISH, "root": PATHISH, "labels": (names, "a list of distinct label names"),
         "unlabelled": (lambda v: type(v) is bool, "true or false")}
REPO = {
    "people": {"me": (text, "a login"), "owner": (text, "a name"), "review_only": (names, "a list of distinct logins"),
               "skip": (names, "a list of distinct logins")},
    "patent": {
        "held_words": (lambda v: words(v) and bool(v), "a non-empty list of distinct lowercase words"),
        "skip_lockfiles": (lambda v: names(v) and bool(v) and not any("/" in x for x in v), "a non-empty list of distinct file names"),
        "private_repo": (repo, "owner/name"),
    },
    "merge": {
        "gate_check": (text, "a check name"),
        "review_label": (text, "a label name"),
        "hold_label": (text, "a label name"),
        "preapproved_labels": (names, "a list of distinct label names"),
    },
    "issues": {
        "fixes": line_with("{issue}"),
        "refs": line_with("{issue}"),
        "salvaged_from": (lambda v: text(v) and v.endswith(" #{pr}") and "{" not in v[:-5], "one line ending in ` #{pr}`, with no other placeholder"),
    },
    "review": {"outside": (lambda v: v in ("salvage", "request-changes"), "salvage or request-changes")},
}


def sections(where, data, schema, extra=(), optional=()):
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise Refused(f"{where}: schema must be 1, not {data.get('schema')!r}")
    for key in data.keys() - schema.keys() - {"schema", *extra}:
        raise Refused(f"unknown key {key}")
    for name, spec in schema.items():
        sec = data.get(name)
        if sec is None and name in optional:
            continue
        if not isinstance(sec, dict) or not sec:
            raise Refused(f"[{name}] is missing or empty")
        table(name, sec, spec)
    return data


def table(name, sec, spec):
    for k in sec.keys() - spec.keys():
        raise Refused(f"unknown key {name}.{k}")
    for k, (check, want) in spec.items():
        if k not in sec:
            raise Refused(f"missing {name}.{k}")
        if not check(sec[k]):
            raise Refused(f"{name}.{k} must be {want}, not {sec[k]!r}")


def validate_machine(data):
    sections("machine", data, MACHINE, extra=("repos",))
    places = data.get("repos")
    if not isinstance(places, dict) or not places:
        raise Refused("[repos] is missing or empty: this machine works on no repo")
    for name, place in places.items():
        if not repo(name) or not isinstance(place, dict):
            raise Refused(f"repos.{name} must be an owner/name table")
        table(f'repos."{name}"', place, PLACE)
    return data


def validate_repo(name, data):
    sections(name, data, REPO, optional=("patent",))
    if "patent" in data and name.lower() == data["patent"]["private_repo"].lower():
        raise Refused("patent.private_repo must differ from merge.repo")
    m = data["merge"]
    if len({m["review_label"], m["hold_label"], *m["preapproved_labels"]}) != 2 + len(m["preapproved_labels"]):
        raise Refused("merge.review_label, merge.hold_label and merge.preapproved_labels must all differ")
    return data


def read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except OSError as e:
        raise Refused(f"cannot read {path}: {e.strerror}") from None
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"{path} is not TOML: {e}") from None


def machine_file():
    me = os.environ.get("ZSTACK_MACHINE") or socket.gethostname().split(".")[0]
    for name in (me, "default"):
        path = os.path.join(CONFIG, "machines", f"{name}.toml")
        if os.path.isfile(path):
            return path
    raise Refused(f"no machine file in {os.path.join(CONFIG, 'machines')}: neither {me}.toml nor default.toml")


def repos():
    base = os.path.join(CONFIG, "repos")
    found = [f"{o}/{n}" for o in sorted(os.listdir(base) if os.path.isdir(base) else [])
             for n in sorted(os.listdir(os.path.join(base, o)) if os.path.isdir(os.path.join(base, o)) else [])
             if os.path.isfile(os.path.join(base, o, n, "policy.toml"))]
    if not found:
        raise Refused(f"no repos/<owner>/<name>/policy.toml in {CONFIG}")
    return found


def origin():
    r = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, env=NO_GIT_ENV)
    m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", r.stdout) if r.returncode == 0 else None
    return m.group(1) if m else None


def pick(here, want=None):
    by = {x.lower(): x for x in here}
    want = want or os.environ.get("ZSTACK_REPO")
    if want:
        if want.lower() not in by:
            raise Refused(f"this machine works on {', '.join(here)}, not {want}")
        return by[want.lower()]
    at = origin()
    if at and at.lower() in by:
        return by[at.lower()]
    if len(here) == 1:
        return here[0]
    raise Refused(f"this machine works on {', '.join(here)}; set ZSTACK_REPO or run from one of their clones")


def load(want=None):
    machine, known = validate_machine(read_toml(machine_file())), repos()
    by = {x.lower(): x for x in known}
    if lost := [x for x in machine["repos"] if x.lower() not in by]:
        raise Refused(f"this machine lists {', '.join(lost)}, which has no repos/<owner>/<name>/policy.toml in {CONFIG}")
    name = by[pick(list(machine["repos"]), want).lower()]
    place = next(v for k, v in machine["repos"].items() if k.lower() == name.lower())
    project = validate_repo(name, read_toml(os.path.join(CONFIG, "repos", name, "policy.toml")))
    every, mine = set(), os.path.basename(machine_file())
    for f in sorted(os.listdir(os.path.join(CONFIG, "machines"))):
        other = read_toml(os.path.join(CONFIG, "machines", f)).get("repos") if f.endswith(".toml") else None
        for k, v in (other.items() if isinstance(other, dict) else []):
            if k.lower() != name.lower() or not isinstance(v, dict) or not names(v.get("labels")):
                continue
            every |= set(v["labels"])
            if "default.toml" in (f, mine) or f == mine:
                continue
            both = sorted(set(v["labels"]) & set(place["labels"]))
            clash = "both pick up every item" if not v["labels"] or not place["labels"] else \
                f"both pick up the label {both[0]}" if both else \
                "both pick up unlabelled items" if v.get("unlabelled") and place["unlabelled"] else None
            if clash:
                raise Refused(f"machines/{mine} and machines/{f} overlap on {name}: {clash}, so two machines would take one item")
    root = (os.environ.get("ONIGIRI_ROOT") or place["root"]).rstrip("/")
    return {"schema": 1, **{k: machine[k] for k in MACHINE}, **{k: project[k] for k in REPO if k in project},
            "merge": {"repo": name, **project["merge"]}, "repos": known,
            "host": {"clone": place["clone"], "root": root, "repos": [by[x.lower()] for x in machine["repos"]],
                     "labels": place["labels"], "unlabelled": place["unlabelled"], "all_labels": sorted(every)}}


def keys(where, d, want, optional=()):
    if not isinstance(d, dict):
        raise Refused(f"{where} must be a table")
    for k in d.keys() - set(want) - set(optional):
        raise Refused(f"unknown key {where}.{k}")
    for k in want:
        if k not in d:
            raise Refused(f"missing {where}.{k}")


def get(data, key):
    v = data
    for part in key.split("."):
        if not isinstance(v, dict) or part not in v:
            raise Refused(f"no key {key}")
        v = v[part]
    return v


def fill(where, s, pol):
    def one(m):
        v, how = get(pol, m.group(1)), m.group(2)
        if v == [] and how:
            return "no one"
        if isinstance(v, list) and all(type(x) in (str, int) for x in v) and how:
            v = [str(x) for x in v]
            return (", ".join(v[:-1]) + f" {how} " + v[-1] if len(v) > 1 else v[0]) if how in ("and", "or") else how.join(v)
        if type(v) not in (str, int) or how:
            why = "a list and needs a joiner" if isinstance(v, list) and not how else "not one value"
            raise Refused(f"invariants: {where} names {m.group(0)}, which is {why}")
        return re.sub(r"\{(\w+)\}", r"<\1>", str(v))
    try:
        out = re.sub(r"\{([a-z_]+(?:\.[a-z_]+)+)(?::([^{}]+))?\}", one, s)
    except Refused as e:
        raise Refused(e.args[0] if e.args[0].startswith("invariants:") else f"invariants: {where}: {e.args[0]} in the config") from None
    if "{" in out or "}" in out:
        raise Refused(f"invariants: {where} has a brace that names no config key")
    return out


def entries(where, data, pol, seen):
    out = {"invariant": [], "guidance": []}
    for kind, want in (("invariant", ("id", "text", "enforced", "test")), ("guidance", ("id", "text"))):
        rows = data.get(kind, [])
        if not isinstance(rows, list):
            raise Refused(f"invariants: {where} {kind} must be a list")
        for r in rows:
            keys(f"{where} {kind}", r, want)
            if not (isinstance(r["id"], str) and re.fullmatch(r"[a-z]+(-[a-z]+)*", r["id"])) or not text(r["text"]) or "\n" in r["text"]:
                raise Refused(f"invariants: {where} {kind} {r.get('id')!r} needs a kebab-case id and one line of text")
            if not all(text(r[k]) for k in want):
                raise Refused(f"invariants: {r['id']} needs each of {', '.join(want)} as text")
            if r["id"] in seen:
                raise Refused(f"invariants: two entries have the id {r['id']}")
            seen.add(r["id"])
            out[kind].append({**r, "text": fill(r["id"], r["text"], pol)})
    return out


def invariants(path=INVARIANTS, want=None):
    pol, data = load(want), read_toml(path)
    keys("invariants", data, ("schema", "intro", "invariant"), ("guidance",))
    if data["schema"] != 1 or not text(data["intro"]) or not data["invariant"]:
        raise Refused("invariants: schema must be 1, intro text, and at least one invariant")
    seen, more = set(), os.path.join(CONFIG, "repos", pol["merge"]["repo"], "invariants.toml")
    stack = entries("the stack's", data, pol, seen)
    there = read_toml(more) if os.path.isfile(more) else {"schema": 1}
    keys(more, there, ("schema",), ("invariant", "guidance"))
    mine = entries(pol["merge"]["repo"] + "'s", there, pol, seen)
    return {"intro": fill("intro", data["intro"], pol), "stack": stack["invariant"], "repo": mine["invariant"],
            "guidance": stack["guidance"] + mine["guidance"]}


def render(data, ids=()):
    every = data["stack"] + data["repo"] + data["guidance"]
    if ids:
        by = {r["id"]: r["text"] for r in every}
        if miss := [i for i in ids if i not in by]:
            raise Refused(f"no invariant or guidance {', '.join(miss)} in {INVARIANTS} or the repo's invariants.toml")
        return "".join(f"- {by[i]}\n" for i in ids)
    out = ["## Invariants", "", data["intro"]]
    for title, part in (("Always", "stack"), ("In this repo", "repo")):
        if data[part]:
            out += ["", f"### {title}", "", *(f"- {r['text']}" for r in data[part])]
    return "\n".join(out) + "\n"


def seats_of(data):
    out = {}
    for name, v in data["models"].items():
        for i, s in enumerate(v, 1) if isinstance(v, list) else [(None, v)]:
            out[name if i is None else f"{name}:{i}"] = s
    return out


def model_for(want, data=None):
    found = seats_of(data or load())
    race = [k for k in found if k.startswith(want + ":")]
    if want not in found and race:
        raise Refused(f"{want} has {len(race)} seats, one per side of a race; pass one of {', '.join(race)}. A race runs every seat.")
    if want not in found:
        raise Refused(f"no role {want!r} in {machine_file()} (roles: {', '.join(found)})")
    return found[want]["provider"], found[want]["model"]


def config_command(argv):
    if argv == ["check"]:
        every = list(validate_machine(read_toml(machine_file()))["repos"])
        for name in every:
            invariants(want=name)
            clone = os.path.expanduser(load(name)["host"]["clone"])
            who = subprocess.run(["git", "-C", clone, "config", "--get", "user.email"], capture_output=True, text=True).stdout
            if os.path.isdir(clone) and not who.strip():
                raise Refused(f"{clone} has no git user.email, so a commit there carries this machine's address: set one the forge knows")
        return print(f"queue: {CONFIG} is valid for {', '.join(every)} on {os.path.basename(machine_file())[:-5]}") or 0
    if argv[:1] == ["invariants"]:
        return print(render(invariants(), argv[1:]), end="") or 0
    if argv == ["warm"]:
        return print("".join(f"zstack: {x}\n" for x in warm(load())), end="") or 0
    if argv == ["dump"]:
        return print(json.dumps(load())) or 0
    if len(argv) == 2 and argv[0] == "seat":
        return print(*model_for(argv[1])) or 0
    if argv == ["seats"]:
        return print("\n".join(seats_of(load()))) or 0
    if len(argv) == 2 and argv[0] == "get":
        v = get(load(), argv[1])
        return print(v if type(v) in (str, int) else json.dumps(v)) or 0
    return None


def share(src, dst):
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.clonefile(os.fsencode(src), os.fsencode(dst), 1):
            raise OSError(os.strerror(ctypes.get_errno()))
        return
    aside = tempfile.mkdtemp(prefix=".zstack-warm-", dir=os.path.dirname(dst))
    try:
        p = subprocess.run(["cp", "-R", "-P", "--reflink=always", "--", src, os.path.join(aside, "it")], capture_output=True)
        if p.returncode:
            raise OSError((os.fsdecode(p.stderr).strip().splitlines() or ["cp --reflink=always failed"])[-1])
        if os.path.lexists(dst):
            raise OSError("something was put there meanwhile")
        os.rename(os.path.join(aside, "it"), dst)
    finally:
        shutil.rmtree(aside)


def git_dirs(where):
    cmd = ["git", "-C", where, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    at = [os.path.realpath(x) for x in p.stdout.splitlines()]
    return at if p.returncode == 0 and len(at) == 2 else (None, None)


def points_back(src):
    inner = [x.path for x in os.scandir(src) if x.is_dir(follow_symlinks=False)] if os.path.isdir(src) and not os.path.islink(src) else []
    return any(os.path.lexists(os.path.join(d, mark)) for d in (src, *inner) for mark in ("pyvenv.cfg", ".git"))


def warm(pol):
    def paths(where, *args, stdin=None):
        p = subprocess.run(["git", "-C", where, *args], capture_output=True, input=stdin)
        return [os.fsdecode(x).rstrip("/") for x in p.stdout.split(b"\0") if x]
    (top, common), (clone, its) = git_dirs("."), git_dirs(os.path.expanduser(pol["host"]["clone"]))
    if not top or top == clone or common != its:
        return []
    there = paths(clone, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory")
    wanted = [x for x in sorted(there) if not os.path.lexists(os.path.join(top, x))]
    asked = b"\0".join(os.fsencode(x + "/" * os.path.isdir(os.path.join(clone, x))) for x in wanted)
    ignored = set(paths(top, "check-ignore", "-z", "--stdin", stdin=asked)) if wanted else set()
    shared, said = [], []
    for path in wanted:
        src, dst, name = os.path.join(clone, path), os.path.join(top, path), re.sub(r"[^\x20-\x7e]", "?", path)
        try:
            if any(os.path.realpath(os.path.dirname(x)) != os.path.dirname(x) for x in (src, dst)):
                raise OSError("a link on the way to it leads elsewhere")
            if points_back(src):
                raise OSError("it holds a Python virtualenv or a checkout, whose files point back at where they are")
            if path not in ignored:
                raise OSError("git does not ignore it here")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            share(src, dst)
            shared.append(name)
        except OSError as e:
            said.append(f"{name} is not shared from the clone: {e}")
    rest = f"`git -C {shlex.quote(top)} ls-files -oi --exclude-standard --directory`"
    more = f" and {len(shared) - 20} more, which {rest} lists" if len(shared) > 20 else ""
    return [f"shared from the clone copy-on-write, as they were there: {', '.join(shared[:20])}{more}"] * bool(shared) + said


def sha():
    r = subprocess.run(["git", "-C", PKG, "rev-parse", "HEAD"], capture_output=True, text=True, env=NO_GIT_ENV)
    return r.stdout.strip() or "unknown"


def now(ms=False):
    at = datetime.now(timezone.utc)
    return at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z" if ms else at.strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return f.read()
    except OSError:
        return None


def root(pol):
    return os.path.abspath(os.path.expanduser(pol["host"]["root"]))


def send(pol, body, attempts=3, timeout=5):
    key = (read_text(pol["wake"]["key_file"]) or "").strip()
    if not key:
        return "webhook not configured"
    req = urllib.request.Request(pol["wake"]["url"], data=json.dumps(body).encode(), method="POST",
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


def wake(pol, kind, message, pr=None, thread=None):
    body = {"kind": kind, "id": thread or os.environ.get(THREAD_VAR) or None, "pr": pr, "reason": message[:1000],
            "time": now(), "source": "queue", "host": socket.gethostname().split(".")[0]}
    if os.environ.get("ONIGIRI_WAKE_DRY"):
        print(json.dumps(body), file=sys.stderr)
        return "not sent: dry run"
    return send(pol, body)


def gh(*args, stdin=None):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, input=stdin, env=ENV)
    return p.returncode, p.stdout, (p.stderr or p.stdout).strip()[-200:]


def gh_json(errors, *args):
    rc, out, err = gh(*args)
    if rc:
        errors.append(f"gh {' '.join(args[:3])}: {err}")
        return []
    try:
        return json.loads(out)
    except ValueError:
        errors.append(f"gh {' '.join(args[:3])}: not JSON")
        return []


def bot(author):
    return bool(author.get("is_bot")) or author["login"].startswith("app/") or author["login"].endswith("[bot]")


def candidates(errors, pol):
    name, me, short = pol["merge"]["repo"], pol["people"]["me"], pol["merge"]["repo"].split("/")[1]
    out = []
    for i in gh_json(errors, "issue", "list", "-R", name, "--assignee", me, "--state", "open", "--limit", "1000",
                     "--json", "number,title,body,url,createdAt,labels,closedByPullRequestsReferences,author"):
        out.append(dict(id=f"{short}-{i['number']}", kind="issue", n=i["number"], head=None, author=i["author"], title=i["title"],
                        body=i.get("body") or "", url=i["url"], created=i["createdAt"], labels=[x["name"] for x in i["labels"]],
                        linked=[x["number"] for x in i.get("closedByPullRequestsReferences") or []]))
    for p in gh_json(errors, "pr", "list", "-R", name, "--state", "open", "--search", f"review-requested:{me}", "--limit", "1000",
                     "--json", "number,title,body,url,createdAt,labels,headRefOid,author"):
        out.append(dict(id=f"{short}-{p['number']}-review-{p['headRefOid'][:7]}", kind="review", n=p["number"], head=p["headRefOid"],
                        author=p["author"], title=p["title"], body=p.get("body") or "", url=p["url"], created=p["createdAt"],
                        labels=[x["name"] for x in p["labels"]], linked=[]))
    return sorted(out, key=lambda c: (c["created"] or "9", c["id"]))


def open_prs(errors, name):
    rows = gh_json(errors, "pr", "list", "-R", name, "--state", "open", "--limit", "3000", "--json", "number,labels")
    return {p["number"]: [x["name"] for x in p["labels"]] for p in rows}


def takes(labels, host):
    mine, hit = set(host["labels"]), sorted(set(labels) & set(host["all_labels"]))
    return hit[0] in mine if hit else (not mine or host["unlabelled"])


def skip(c, q, prs, pol):
    hold = pol["merge"]["hold_label"]
    held_prs = {n for n, labels in prs.items() if hold in labels}
    for part in ("running", "waiting", "tasks", "done"):
        if c["id"] in q[part]:
            return part
    if not takes(c["labels"], pol["host"]):
        return "other-machine"
    if bot(c["author"]) or c["author"]["login"].lower() in [x.lower() for x in pol["people"]["skip"]]:
        return "skipped-author"
    if hold in c["labels"] or any(n in held_prs for n in c["linked"]):
        return "held"
    if c["kind"] == "issue" and any(n in prs for n in c["linked"]):
        return "pr"
    busy = {n for part in ("running", "waiting", "tasks") for i in q[part].values() for n in (i.get("n"), i.get("pr")) if n}
    if c["n"] in busy or any(n in busy for n in c["linked"]):
        return "overlap"
    return None


def teammate(c, pol):
    return c["kind"] == "review" and c["author"]["login"].lower() in [x.lower() for x in pol["people"]["review_only"]]


def task(c, pol):
    name, me, iss = pol["merge"]["repo"], pol["people"]["me"], pol["issues"]
    if c["kind"] == "issue":
        return (f"{name}#{c['n']}: {c['title']}\n{c['url']}\n\nKind: fix in {name}, picked from the issues assigned to {me}. "
                f"Read the issue and its thread, fix it with a test, and open a PR whose body says `{iss['fixes'].format(issue=c['n'])}`.")
    salvage = pol["review"]["outside"] == "salvage"
    outside = "salvage a fixable PR as a new PR of ours that credits them, instead of requesting changes" if salvage else \
        "request changes on a fixable PR, and do not rewrite it as a PR of ours"
    mate = teammate(c, pol)
    who = "teammate (review-only: a first review and nothing else; the guard refuses every push and post from this thread)" if mate else \
        f"outside contributor ({outside})"
    said = (f"{name}#{c['n']} at head {c['head']} by {c['author']['login']}, a {who}: {c['title']}\n{c['url']}\n\nKind: first "
            f"review, requested of {me}. Run playbooks/pull-requests.md Verify. Post nothing under the owner's name: put every "
            "comment for the author under a `drafts:` section of your final report.")
    if mate or not salvage:
        return said
    frm = iss["salvaged_from"].format(pr=c["n"])
    return (f"{said}\nA salvage PR's body carries `{frm}` and a `{iss['fixes'].format(issue='<n>')}` line for each issue "
            f"#{c['n']} fixes or was opened for, so the forge closes them when the salvage merges. Take those issues from "
            f"#{c['n']}'s own body; an issue it names but says it does not fix gets `{iss['refs'].format(issue='<n>')}` instead.\n"
            f"A salvage keeps {c['author']['login']}'s commits as authored: cherry-pick or rebase their commits, then put our "
            "fixes on top as separate commits. Never re-author their work or squash it into our commit. If none of their "
            "commits survive, add `Co-authored-by: <their name> <their GitHub noreply or commit email>` for them to our "
            f"commit; the body still says `{frm}`.")


KEYS = ("running", "waiting", "tasks", "verdicts", "done")


def locked(pol, fn):
    base = root(pol)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "queue.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = os.path.join(base, "queue.json")
        rows = read_text(path)
        q = json.loads(rows) if rows else {**{k: {} for k in KEYS}, "done": []}
        if set(q) != set(KEYS):
            raise Refused(f"{path} was written by an older zstack: move it aside, and the queue starts empty")
        out, changed = fn(q)
        rows = q.pop("ended", [])
        if changed:
            with open(path + ".tmp", "w") as f:
                json.dump(q, f, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(path + ".tmp", path)
        if rows:
            try:
                with open(os.path.join(base, "events.log"), "a") as f:
                    f.writelines(rows)
            except OSError as e:
                print(f"zstack: events.log was not written ({e}): " + "".join(rows), end="", file=sys.stderr)
        return out


def work(pol, q):
    errors = []
    cands, prs = candidates(errors, pol), open_prs(errors, pol["merge"]["repo"])
    ready, skipped = [], {}
    for c in [] if errors else cands:
        why = skip(c, q, prs, pol)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        pre = c["kind"] == "issue" and bool(set(c["labels"]) & set(pol["merge"]["preapproved_labels"]))
        ready.append({"id": c["id"], "kind": c["kind"], "n": c["n"], "priority": rank(c["labels"]), "at": c["created"],
                      "preapproved": pre, "task": task(c, pol), "title": c["title"], "readonly": teammate(c, pol)})
    ready += [{"id": k, "kind": "task", "n": None, "readonly": False, **t} for k, t in q["tasks"].items() if k not in q["running"]]
    ready.sort(key=lambda x: (-x["priority"], x["at"], x["id"]))
    return ready, skipped, errors, {c["id"] for c in cands}


def rank(labels):
    return 100 if "urgent" in labels else 0


def first(pol, q):
    if len(q["running"]) >= pol["run"]["threads"]:
        return None, "the machine is at its thread cap", {}, None
    ready, skipped, errors, listed_ids = work(pol, q)
    if errors:
        return None, errors[0], skipped, None
    return (ready[0], None, skipped, listed_ids) if ready else (None, "nothing is ready", skipped, listed_ids)


def add(pol, ident, said, priority, preapproved=False):
    if not re.fullmatch(ID, ident) or not said.strip():
        raise Refused("an id of letters, digits, . _ - and a task on stdin")

    def go(q):
        for part in ("tasks", "running", "waiting"):
            if ident in q[part]:
                raise Refused(f"{ident} is already {'a task' if part == 'tasks' else part}")
        q["tasks"][ident] = {"task": said.strip(), "title": said.strip().splitlines()[0][:120], "priority": priority,
                             "preapproved": preapproved, "at": now()}
        return q["tasks"][ident], True
    return locked(pol, go)


def approve(pol, ident):
    def go(q):
        parts = [p for p in ("running", "waiting", "tasks") if ident in q[p]]
        if not parts:
            raise Refused(f"{ident} is not a task, running or waiting, so there is nothing to approve")
        if any(q[p][ident].get("thread") and os.environ.get(THREAD_VAR) == q[p][ident]["thread"] for p in parts):
            raise Refused(f"this is {ident}'s own thread: only the owner preapproves an item")
        unblocked = next((q[p][ident]["refused"]["refs"] for p in parts if q[p][ident].get("refused")), None)
        for p in parts:
            q[p][ident]["preapproved"] = True
            if q[p][ident].get("refused"):
                q[p][ident].update(refused=None, complete=False)
        return {"id": ident, "preapproved": parts[0], **({"unblocked": unblocked} if unblocked else {})}, True
    return locked(pol, go)


BRIEF = """# {id}

{task}

repo: a worktree of `{clone}`

Follow onigiri-mode and the invariants, which are already in your context. Nothing enforces these, so they are yours to judge:
{guidance}

When your PR is open and you believe it is done, have the verifier check its head, and wait for its answer inside this turn: do not end your turn while it runs. Put this line in its brief with the PR number and the full head sha filled in: `{env} {queue} verified {id} <pr number> <head sha> auto`, `review` or `fail`. The verifier runs it as its last step, with those variables, since its session may not have yours; you never do.
The queue answers that command with what it recorded and one line under `say`, and the verifier gives you both. That line decides what you do, whatever else the verifier wrote: fix the findings, push and have the new head checked; or merge with the command it names, waiting first for the checks it names; or do not merge, for the reason it gives. The guard lets the merge through only when the queue would; when it refuses, read its reason, and do not try another way. When you think the change needs a person whatever the queue says, do not merge: say why in your report.
When your turn ends, the queue frees your slot and tells the orchestrator what happened, so finish everything before you stop. A turn that tries to end while your open PR's head has no verdict is sent back to get one.
A turn that ends with no PR sends this item back to tasks to run again, unless the pre-push hook refused your push, which leaves it waiting for the owner, or you mark it complete first with `{env} {queue} complete {id}`. Run that only once the deliverable exists, such as the answer in your final report or the file the task asks for, never while work it waits on still runs.

End with a final report: the PR URLs, then
follow-ups:
questions:
"""  # noqa: E501


def brief(pol, it):
    env = {"ZSTACK_CONFIG": CONFIG, "ZSTACK_MACHINE": os.environ.get("ZSTACK_MACHINE"), "ZSTACK_REPO": pol["merge"]["repo"]}
    said = "\n".join(f"- {r['text']}" for r in invariants(want=pol["merge"]["repo"])["guidance"])
    return BRIEF.format(id=it["id"], task=it["task"], clone=pol["host"]["clone"], queue=os.path.join(PKG, "queue.py"),
                        guidance=said, env=" ".join(f"{k}={shlex.quote(v)}" for k, v in env.items() if v))


DEAD = ("completed", "failed", "interrupted", "cancelled")
DIED = ("failed", "interrupted", "cancelled")


def t3(pol, *args, stdin=None):
    cmd = [os.path.expanduser(pol["run"]["t3"]), "thread", *args, "--json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, input=stdin, env=ENV, timeout=START_SECONDS)
        return json.loads(p.stdout) if p.returncode == 0 else (p.stderr or p.stdout).strip()[-200:]
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return f"{type(e).__name__}: {e}"[:200]


def launch(pol, up):
    clone = os.path.expanduser(pol["host"]["clone"])
    if git("-C", clone, "fetch", "--quiet", "origin")[0]:
        raise Refused(f"git fetch origin failed in {clone}; not launching on an old tree")
    rc, base = (0, up["base"]) if up.get("base") else git("-C", clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc:
        raise Refused(f"{clone} has no origin/HEAD, so its default branch is unknown: run `git remote set-head origin --auto` there")
    provider, model = model_for("coding", pol)
    got = t3(pol, "start", clone, "-", "--title", up["id"], "--provider", provider, "--model", model, "--worktree", base.strip(),
             stdin=brief(pol, up))
    if not isinstance(got, dict):
        raise Refused(f"no thread started for {up['id']}: {got}")
    seen = t3(pol, "show", got["threadId"], "--messages", "0")
    return got["threadId"], seen["thread"]["worktreePath"] if isinstance(seen, dict) else None


def step(pol):
    def intend(q):
        was = json.dumps(q, sort_keys=True)
        unresume(q)
        cleared, kept = settle(pol, q)
        up, waiting, skipped, listed_ids = first(pol, q)
        gone = [d for d in q["done"] if listed_ids is not None and d not in listed_ids]
        q["done"] = [d for d in q["done"] if d not in gone]
        if up:
            q["running"][up["id"]] = {**{k: up[k] for k in ("kind", "n", "preapproved", "readonly", "title", "priority")},
                                      **{k: up[k] for k in ("dropped", "seen", "queued", "kept") if k in up},
                                      "thread": None, "worktree": None, "pr": up.get("pr"), "at": now(ms=True), "logged": True}
            if up["kind"] == "task":
                q["running"][up["id"]]["task"] = q["tasks"][up["id"]]
        said = {"launched": None, "waiting": waiting, "cleared": cleared, "kept": kept, "skipped": skipped}
        return (up, said), bool(gone or up) or json.dumps(q, sort_keys=True) != was
    up, out = locked(pol, intend)
    if not up:
        return out
    try:
        thread, worktree = launch(pol, up)
    except Refused as e:
        return dict(out, waiting=f"{e}; a later pass takes the thread if one did start, and launches {up['id']} again if none shows")

    def record(q):
        q["tasks"].pop(up["id"], None)
        q["running"][up["id"]].update(thread=thread, worktree=worktree)
        return dict(out, launched={"id": up["id"], "thread": thread, "worktree": worktree}), True
    return locked(pol, record)


def reconcile(pol):
    def go(q):
        dead, changed, rows = [], False, None

        def listing():
            got = t3(pol, "list", "--project", os.path.expanduser(pol["host"]["clone"]))
            if not isinstance(got, list):
                raise Refused(f"T3 Code does not answer, so no thread can be read or launched: {got}")
            return got
        for key, it in sorted(q["running"].items()):
            if not it["thread"]:
                rows = listing() if rows is None else rows
                mine = [r["threadId"] for r in rows if r["title"] == key and r["createdAt"] >= it["at"]]
                asked = datetime.strptime(it["at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                if not mine and (datetime.now(timezone.utc) - asked).total_seconds() >= pol["run"]["lost_min"] * 60:
                    del q["running"][key]
                    changed = True
                if not mine:
                    continue
                it["thread"], changed = mine[0], True
                q["tasks"].pop(key, None)
            seen = t3(pol, "show", it["thread"], "--messages", "0")
            if not isinstance(seen, dict):
                rows = listing() if rows is None else rows
                dead += [(key, True)] if it["thread"] not in [r["threadId"] for r in rows] else []
                continue
            if not it["worktree"] and seen["thread"]["worktreePath"]:
                it["worktree"], changed = seen["thread"]["worktreePath"], True
            if seen["thread"]["status"] in DEAD:
                dead.append((key, seen["thread"]["status"] in DIED))
        return dead, changed
    return locked(pol, go)


def run_pass(pol):
    os.makedirs(root(pol), exist_ok=True)
    with open(os.path.join(root(pol), "pass.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused("another pass is running on this machine") from None
        out = {"released": [], "launched": [], "cleared": [], "kept": []}
        for key, died in reconcile(pol):
            try:
                out["released"].append(release(pol, key, died=died))
            except Refused:
                pass
        while True:
            got = step(pol)
            out["cleared"] += got["cleared"]
            out["kept"] = got["kept"]
            if not got["launched"]:
                return dict(out, waiting=got["waiting"], skipped=got["skipped"])
            out["launched"].append(got["launched"])


def pr_view(pol, pr):
    fields = "state,headRefOid,headRefName,baseRefName,isCrossRepository,labels,number,isDraft,mergeable,statusCheckRollup"
    rc, out, err = gh("pr", "view", str(pr), "-R", pol["merge"]["repo"], "--json", fields)
    if rc:
        raise Refused(f"cannot read PR {pr}: {err}")
    return json.loads(out)


def gate_green(pol, live):
    name = pol["merge"]["gate_check"]
    return any((c.get("name") or c.get("context")) == name and (c.get("conclusion") or c.get("state")) == "SUCCESS"
               for c in live.get("statusCheckRollup") or [])


def labels_of(pol, it, live):
    mine = [x["name"] for x in live["labels"]]
    if not it or it["kind"] != "issue":
        return mine
    rc, out, err = gh("issue", "view", str(it["n"]), "-R", pol["merge"]["repo"], "--json", "labels")
    if rc:
        raise Refused(f"cannot read issue {it['n']}, so whether it is held is unknown: {err}")
    return mine + [x["name"] for x in json.loads(out)["labels"]]


def held(pol, it, live):
    hold = pol["merge"]["hold_label"]
    return hold in [x["name"] for x in live["labels"]] or hold in labels_of(pol, it, live)


def stopped(pol, it, v, live):
    if not v or v["result"] == "fail" or v["head"] != live["headRefOid"]:
        return f"the verifier has not passed head {live['headRefOid']}"
    pre = bool(it) and it["preapproved"]
    if not pre and (v["result"] != "auto" or pol["merge"]["review_label"] in [x["name"] for x in live["labels"]]):
        return "it needs a person's review"
    if held(pol, it, live):
        return f"it is labelled {pol['merge']['hold_label']}"
    if ((it or {}).get("dropped") or {}).get("head") == live["headRefOid"]:
        return it["dropped"]["why"]
    return "the PR is a draft" if live.get("isDraft") else None


def unready(pol, live):
    if not gate_green(pol, live):
        return f"{pol['merge']['gate_check']} has not passed on that head"
    running = any(c.get("status", "COMPLETED") != "COMPLETED" and not (c.get("conclusion") and c.get("completedAt"))
                  or c.get("state") in ("PENDING", "EXPECTED") for c in live.get("statusCheckRollup") or [])
    return "a check is still running on that head, and a merge now would arm auto-merge" if running else None


def blocked(pol, it, v, live):
    return stopped(pol, it, v, live) or unready(pol, live)


def move(it, pr):
    if it["pr"] != pr:
        for k in ("queued", "seen", "dropped"):
            it.pop(k, None)
    it["pr"] = pr


def owner_of(q, pr):
    return next((v for part in ("running", "waiting") for v in q[part].values() if v["pr"] == pr), None)


def held_pr(q, pr):
    return owner_of(q, pr) or any(t.get("pr") == pr for t in q["tasks"].values())


def verified(pol, ident, pr, head, result):
    def go(q):
        it = q["running"].get(ident) or q["waiting"].get(ident)
        if not it:
            raise Refused(f"{ident} is not running or waiting on its PR, so no thread has run it")
        if it["thread"] and os.environ.get(THREAD_VAR) == it["thread"]:
            raise Refused(f"this is {ident}'s own thread: the verifier it starts records the verdict, never the thread")
        live = pr_view(pol, pr)
        if live["headRefOid"] != head:
            raise Refused(f"PR {pr} is at {live['headRefOid']}, not {head}: the head moved, so check the new one")
        move(it, pr)
        q["verdicts"] = {k: v for k, v in q["verdicts"].items() if held_pr(q, int(k))}
        q["verdicts"][str(pr)] = {"head": head, "result": result}
        out = {"id": ident, "pr": pr, "recorded": result}
        if result == "fail":
            return dict(out, say="its thread fixes the findings and has the new head checked"), True
        why = stopped(pol, it, q["verdicts"][str(pr)], live)
        if why:
            return dict(out, say=f"its thread does not merge it: {why}"), True
        cmd, wait = f"gh pr merge {pr} -R {pol['merge']['repo']} --squash --match-head-commit {head}", unready(pol, live)
        return dict(out, say=f"its thread merges it with `{cmd}`" + (f", but not yet: {wait}" if wait else "")), True
    return locked(pol, go)


def gate(pol, pr, head):
    def go(q):
        live = pr_view(pol, pr)
        if live["headRefOid"] != head:
            raise Refused(f"PR {pr} is at {live['headRefOid']}, not {head}")
        it = owner_of(q, pr)
        why = blocked(pol, it, q["verdicts"].get(str(pr)), live) if it else f"the verifier has not passed head {head}"
        if why:
            raise Refused(f"PR {pr} does not merge: {why}")
        return {"merge": pr, "head": head}, False
    return locked(pol, go)


def git(*args, cwd=None, raw=False):
    p = subprocess.run(["git", *args], capture_output=True, text=not raw, env=ENV, cwd=cwd, timeout=300)
    if raw:
        enc = locale.getpreferredencoding(False)
        out, _ = p.stdout.decode(enc), p.stderr.decode(enc)
        return p.returncode, out
    return p.returncode, p.stdout


def listed(pol, path):
    clone = os.path.realpath(os.path.expanduser(pol["host"]["clone"]))
    path = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return clone, None
    rc, out = git("-C", clone, "worktree", "list", "--porcelain")
    if rc:
        raise Refused(f"cannot list the worktrees of {clone}")
    return (clone, path) if path != clone and f"worktree {path}" in out.splitlines() else (clone, None)


def tidy(pol, it, whole):
    try:
        clone, path = listed(pol, it["worktree"]) if it["worktree"] else (None, None)
    except Refused:
        return not whole
    if not path:
        return True
    if not whole:
        git("-C", path, "clean", "-Xfdq")
        return True
    dirty, ahead = git("-C", path, "status", "--porcelain"), git("-C", path, "rev-list", "-1", "HEAD", "--not", "--remotes")
    return not (dirty[0] or ahead[0] or dirty[1].strip() or ahead[1].strip()) and not git("-C", clone, "worktree", "remove", path)[0]


def log_push(pol, url, ref, head, cwd=None, refused=False):
    top = cwd or os.getcwd()
    os.makedirs(root(pol), exist_ok=True)
    with open(os.path.join(root(pol), "pushes.jsonl"), "a") as f:
        f.write(json.dumps({"worktree": os.path.realpath(top), "thread": os.environ.get(THREAD_VAR), "url": url, "ref": ref, "head": head,
                            "at": now(ms=True), **({"refused": True} if refused else {})}) + "\n")


def pushed_refs(pol, it, refused=False):
    path, log = os.path.realpath(os.path.expanduser(it["worktree"])), os.path.join(root(pol), "pushes.jsonl")
    text = read_text(log) if os.path.lexists(log) else ""
    try:
        rows = [json.loads(x) for x in text.splitlines()]
        return sorted({r["ref"] for r in rows if (r["worktree"] == path or it.get("thread") and r.get("thread") == it["thread"])
                       and r["at"] >= it["at"] and r.get("refused", False) == refused})
    except (AttributeError, ValueError, KeyError, TypeError):
        return None


def hooked(pol):
    clone = os.path.expanduser(pol["host"]["clone"])
    rc, hook = git("-C", clone, "rev-parse", "--git-path", "hooks/pre-push")
    return not rc and os.path.realpath(os.path.join(clone, hook.strip())) == os.path.realpath(os.path.join(PKG, "hooks", "pre-push"))


def head_pr(pol, refs):
    for ref in refs:
        rc, out, err = gh("pr", "list", "-R", pol["merge"]["repo"], "--head", ref.removeprefix("refs/heads/"), "--state", "all",
                          "--limit", "1", "--json", "number")
        if rc:
            raise Refused(f"cannot look up the PR of {ref}: {err}")
        found = json.loads(out) if out.strip() else []
        if found:
            return found[0]["number"]
    return None


def branch_pr(pol, it):
    path = listed(pol, it["worktree"])[1] if it["worktree"] else None
    if not path:
        return None
    rc, name = git("-C", path, "symbolic-ref", "--short", "-q", "HEAD")
    name = name.strip()
    if rc or not name:
        raise Refused(f"its worktree {path} is on no branch, so its PR cannot be looked up")
    for state in ("open", "all"):
        rc, out, err = gh("pr", "list", "-R", pol["merge"]["repo"], "--head", name, "--state", state, "--limit", "1", "--json", "number")
        if rc:
            raise Refused(f"cannot look up the PR of branch {name}: {err}")
        found = json.loads(out) if out.strip() else []
        if found:
            return found[0]["number"]
    return None


def its_pr(pol, q, it):
    it["pr"] = it["pr"] or branch_pr(pol, it)
    live = pr_view(pol, it["pr"]) if it["pr"] else None
    other = branch_pr(pol, it) if live and live["state"] != "OPEN" else None
    if other and other != it["pr"]:
        q["verdicts"].pop(str(it["pr"]), None)
        move(it, other)
        live = pr_view(pol, other)
    return live


FRESH = ("query($o: String!, $n: String!, $p: Int!, $c: String) { repository(owner: $o, name: $n) { pullRequest(number: $p) { id headRefOid "
         "isDraft mergeable isMergeQueueEnabled labels(first: 100) { nodes { name } } commits(last: 1) { nodes { commit { statusCheckRollup { "
         "contexts(first: 100, after: $c) { pageInfo { hasNextPage endCursor } nodes { ... on CheckRun { name status conclusion completedAt } "
         "... on StatusContext { context state } } } } } } } }")
FRESH_PAGES = 20
QUEUED = ("query($o: String!, $n: String!, $p: Int!) { repository(owner: $o, name: $n) { pullRequest(number: $p) { id state headRefOid "
          "isInMergeQueue added: timelineItems(last: 1, itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT]) { filteredCount } "
          "removed: timelineItems(last: 1, itemTypes: [REMOVED_FROM_MERGE_QUEUE_EVENT]) { filteredCount nodes { "
          "... on RemovedFromMergeQueueEvent { reason } } } } } }")
ENQUEUE = ("mutation($id: ID!, $h: GitObjectID!) { enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $h}) { "
           "clientMutationId } }")
DEQUEUE = "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }"


def approved_merge(pol, it, v, live):
    why = blocked(pol, it, v, live)
    if why or not it["preapproved"]:
        return why or "its thread did not merge it"
    (owner, name), m = pol["merge"]["repo"].split("/"), pol["merge"]
    issue = f" issue(number: {it['n']}) {{ labels(first: 100) {{ nodes {{ name }} }} }}" if it["kind"] == "issue" else ""
    heads, labels, nodes, after = set(), set(), [], ()
    for _ in range(FRESH_PAGES):
        rc, out, err = gh("api", "graphql", "-f", f"query={FRESH}{issue} }} }}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={it['pr']}",
                          *after, "--jq", ".data.repository")
        try:
            got = json.loads(out) if not rc else None
            pr, rollup = got["pullRequest"], (got["pullRequest"]["commits"]["nodes"] or [{}])[0]["commit"]["statusCheckRollup"]
            if rollup is None and after:
                raise TypeError
            checks = (rollup or {"contexts": {"pageInfo": {"hasNextPage": False}, "nodes": []}})["contexts"]
            labels.update(x["name"] for x in pr["labels"]["nodes"] + (got["issue"]["labels"]["nodes"] if issue else []))
            more = checks["pageInfo"]["hasNextPage"]
            if not all(isinstance(c, dict) for c in checks["nodes"]) or more and not isinstance(checks["pageInfo"]["endCursor"], str):
                raise TypeError
        except (ValueError, KeyError, IndexError, TypeError):
            return f"it could not be read again just before the merge: {err or 'not the answer asked for'}"
        heads.add(pr["headRefOid"])
        nodes += checks["nodes"]
        if not more:
            break
        after = ("-f", f"c={checks['pageInfo']['endCursor']}")
    else:
        return f"it could not be read again just before the merge: its checks run past {FRESH_PAGES} pages"
    moved = sorted(heads - {v["head"]})
    why = (f"the verifier has not passed head {moved[0]}" if moved else
           f"it is labelled {m['hold_label']}" if m["hold_label"] in labels else
           "it needs a person's review" if m["review_label"] in labels else
           "the PR is a draft" if pr["isDraft"] else
           unready(pol, {"statusCheckRollup": nodes}) or
           (f"GitHub says it is {(pr['mergeable'] or 'unknown').lower()}, not mergeable" if pr["mergeable"] != "MERGEABLE" else None))
    if why:
        return why
    if pr["isMergeQueueEnabled"]:
        rc, _, err = gh("api", "graphql", "-f", f"query={ENQUEUE}", "-f", f"id={pr['id']}", "-f", f"h={v['head']}")
        if rc:
            return f"the pass could not put it in the merge queue: {err}"
        it["queued"], it["seen"][0] = v["head"], it["seen"][0] + 1
        return None
    rc, _, err = gh("pr", "merge", str(it["pr"]), "-R", m["repo"], "--squash", "--match-head-commit", v["head"])
    return f"the pass could not merge it: {err}" if rc else None


def in_queue(pol, key, it, live):
    (owner, name), pr = pol["merge"]["repo"].split("/"), it["pr"]
    rc, out, err = gh("api", "graphql", "-f", f"query={QUEUED}", "-f", f"o={owner}", "-f", f"n={name}", "-F", f"p={pr}",
                      "--jq", ".data.repository.pullRequest")
    try:
        got = json.loads(out) if not rc else None
        on, state, seen = got["isInMergeQueue"], got["state"], [got["added"]["filteredCount"], got["removed"]["filteredCount"]]
        reason = (got["removed"]["nodes"] or [{}])[-1].get("reason")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise Refused(f"PR {pr}: whether it is in the merge queue could not be read: {err or 'not the answer asked for'}") from None
    if state != "OPEN":
        return f"PR {pr} is {state.lower()}, and the next pass finishes it"
    if on:
        if it.get("queued") != got["headRefOid"]:
            it["queued"], it["seen"] = got["headRefOid"], seen
        try:
            labels, m = labels_of(pol, it, live), pol["merge"]
            why = (f"it is labelled {m['hold_label']}" if m["hold_label"] in labels else
                   "it needs a person's review" if m["review_label"] in labels else None)
        except Refused as e:
            why = str(e)
        if not why:
            return f"PR {pr} is in the merge queue at head {it['queued']}"
        rc, _, err = gh("api", "graphql", "-f", f"query={DEQUEUE}", "-f", f"id={got['id']}")
        if rc:
            return f"PR {pr} stays in the merge queue though {why}: the pass could not take it out: {err}"
        it["queued"] = None
        return f"PR {pr} waits for the owner: {why}, so the pass took it out of the merge queue"
    if not it.get("queued"):
        it["seen"] = seen
        return None
    why = (f"GitHub took head {it['queued']} out of the merge queue, and an entry the pass did not add since, so whose reason is whose "
           "cannot be told" if seen[0] > it["seen"][0] else
           f"GitHub took head {it['queued']} out of the merge queue: {(reason if seen[1] > it['seen'][1] else None) or 'it gave no reason'}")
    it["dropped"], it["queued"] = {"head": it["queued"], "why": why}, None
    said = f"PR {pr} waits for the owner: {why}"
    wake(pol, "blocked", f"{key}: {said}", pr, it["thread"])
    return said


RED = ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")


def needs(pol, it, v, live):
    if it["readonly"] or it["kind"] == "review" and it["pr"] == it["n"] or live.get("isCrossRepository"):
        return []
    if held(pol, it, live) or live.get("isDraft"):
        return []
    head, base, gate_name = live["headRefOid"], live.get("baseRefName") or "its base", pol["merge"]["gate_check"]
    passed = bool(v) and v["result"] != "fail" and v["head"] == head
    if pol["merge"]["review_label"] in labels_of(pol, it, live) or not it["preapproved"] and passed and v["result"] != "auto":
        return []
    out = []
    if (it.get("dropped") or {}).get("head") == head:
        out.append(("a drop from the merge queue", f"{it['dropped']['why']}. Merge origin/{base} into the branch, settle any conflict, and push."))
    elif live.get("mergeable") == "CONFLICTING":
        out.append(("a merge conflict", f"GitHub says the PR conflicts with {base}. Merge origin/{base} into the branch, settle the conflicts, "
                                        "and push."))
    if any((c.get("name") or c.get("context")) == gate_name and (c.get("conclusion") or c.get("state")) in RED
           for c in live.get("statusCheckRollup") or []):
        out.append((f"{gate_name} red", f"{gate_name} failed on head {head}. Read its logs, fix what the diff caused, rerun a job that failed "
                                         "for a reason outside the diff, and push."))
    if not passed:
        failed = bool(v) and v["result"] == "fail" and v["head"] == head
        out.append((f"no pass for head {head[:12]}", f"The verifier failed head {head}: fix its findings and push." if failed else
                    f"The verifier has not passed head {head}: have it check the head you end on."))
    return out


def ended(pol, it):
    if not it["thread"]:
        return True
    seen = t3(pol, "show", it["thread"], "--messages", "0")
    if isinstance(seen, dict):
        return seen["thread"]["status"] in DEAD
    rows = t3(pol, "list", "--project", os.path.expanduser(pol["host"]["clone"]))
    return isinstance(rows, list) and it["thread"] not in [r["threadId"] for r in rows]


def shared(pol, q, it):
    def on(x):
        return it["pr"] in (x.get("pr"), x.get("n"))
    return any(on(x) for p in ("running", "tasks") for x in q[p].values()) or \
        any(on(x) and (x.get("resumed") or not ended(pol, x)) for x in q["waiting"].values())


def resume(pol, q, key, it, live, need):
    repo, pr, branch, row = pol["merge"]["repo"], it["pr"], live["headRefName"], it.get("task") or {}
    was = row.get("was") or row.get("task") or f"{repo}#{it['n']}: {it['title']}"
    keep = {k: it[k] for k in ("dropped", "seen", "queued") if k in it}
    priority = it["priority"] if "priority" in it else row["priority"] if "priority" in row else rank(labels_of(pol, it, live))
    common = {"was": was, "title": it["title"], "priority": priority, "preapproved": it["preapproved"],
              "at": row.get("at") or it["at"], "kind": it["kind"], "n": it["n"], "pr": pr, **keep}
    kept = [x for x in it.get("kept", []) if listed(pol, x)[1]] + ([] if tidy(pol, it, True) else [it["worktree"]])
    told = "".join(f"\nAn old thread's worktree {x} still holds work that is not on the remote: read it with `git -C {shlex.quote(x)} status` and "
                   f"`git -C {shlex.quote(x)} log --branches --not --remotes`, and take what belongs to PR #{pr}." for x in kept)
    said = (f"{was}\n\nThis item ran before, and its thread ended with PR #{pr} open: https://github.com/{repo}/pull/{pr}. "
            "The PR waits on this item's own work, so it runs again in a fresh thread, and where the brief above says to open a PR, "
            f"PR #{pr} is that PR:\n" + "".join(f"- {x[1]}\n" for x in need) +
            f"Your worktree starts at origin/{branch}, the PR's head branch, on a branch of its own: push with "
            f"`git push origin {shlex.quote('HEAD:' + branch)}`, so the commits land on PR #{pr}, and open no new PR.{told}")
    q["tasks"][key] = {"task": said, **common, "base": f"origin/{branch}", "kept": kept}
    return "tasks", f"its thread has ended, and PR {pr} waits on that thread's work ({', '.join(x[0] for x in need)}), so it goes back to tasks"


def complete(pol, ident):
    thread = os.environ.get(THREAD_VAR)

    def go(q):
        it = q["running"].get(ident)
        if not it:
            raise Refused(f"{ident} is not running, so there is no turn of it to mark complete")
        if not thread or it["thread"] != thread:
            raise Refused(f"this is not {ident}'s own thread: only the thread that made its deliverable marks it complete")
        it["complete"] = True
        return {"complete": ident}, True
    return locked(pol, go)


def outcome(pol, q, key, merge=False, died=False):
    ran = key in q["running"]
    it = q["running"].pop(key, None) or q["waiting"].pop(key)
    died = died or it.pop("died", False)
    back = died or not it.get("complete")
    ended_as = "its thread died" if died else "its thread ended, not marked complete,"

    def wait(why):
        q["waiting"][key] = {**it, "died": True} if died else it
        return "waiting", why
    if back and not it["worktree"] and it["thread"]:
        seen = t3(pol, "show", it["thread"], "--messages", "0")
        it["worktree"] = seen["thread"]["worktreePath"] if isinstance(seen, dict) else None
    try:
        live = its_pr(pol, q, it)
        refs = pushed_refs(pol, it) if back and not live and it.get("logged") and it["worktree"] and hooked(pol) else None
        found = head_pr(pol, refs or [])
        if found:
            move(it, found)
            live = pr_view(pol, found)
        was = (it.get("dropped") or {}).get("head")
        said = in_queue(pol, key, it, live) if merge and live and live["state"] == "OPEN" else None
        if said:
            v = q["verdicts"].get(str(it["pr"]))
            need = needs(pol, it, v, live) if (it.get("dropped") or {}).get("head") != was else []
            if need and ended(pol, it) and not shared(pol, q, it):
                return resume(pol, q, key, it, live, need)
            tidy(pol, it, False)
            return wait(said)
        if live and live["state"] == "OPEN":
            v = q["verdicts"].get(str(it["pr"]))
            why = approved_merge(pol, it, v, live) if merge else blocked(pol, it, v, live) or "its thread did not merge it"
            if not why:
                live, why = pr_view(pol, it["pr"]), "the pass merged it, and GitHub still shows it open"
        if live and live["state"] == "OPEN":
            need = needs(pol, it, v, live) if merge and not it.get("queued") else []
            if need and ended(pol, it) and not shared(pol, q, it):
                return resume(pol, q, key, it, live, need)
            tidy(pol, it, False)
            return wait(f"PR {it['pr']} is in the merge queue at head {it['queued']}" if it.get("queued") else
                        f"PR {it['pr']} waits for the owner: {why}")
    except Refused as e:
        return wait(str(e))
    fresh = "refused" not in it and not live and it.get("logged") and it["worktree"]
    stopped_at = pushed_refs(pol, it, refused=True) if fresh and hooked(pol) else None
    if stopped_at:
        why = (f"its thread {'died' if died else 'ended'} with no PR after the pre-push hook refused its push of {', '.join(stopped_at)}, "
               f"since no allowed remote is pushable, so it waits for the owner, its work in {it['worktree']}, and runs again once the "
               f"owner runs `queue.py approve {key}`")
        it["refused"] = {"refs": stopped_at, "worktree": it["worktree"], "why": why}
        row = f"{now()} blocked {key} refs={','.join(stopped_at)} worktree={it['worktree']} {why}"
        q.setdefault("ended", []).append(row.replace("\r", " ").replace("\n", " ") + "\n")
        if not ran:
            wake(pol, "blocked", f"{key}: {why}", None, it["thread"])
    if not live and it.get("refused"):
        return wait(it["refused"]["why"])
    why = f"PR {it['pr']} {live['state'].lower()}" if live else "nothing to merge"
    if died and not live and refs is None:
        why = "its thread died with no record of its pushes, so whether it opened a PR cannot be told"
    elif died and not live and refs:
        why = f"its thread died after it pushed {', '.join(refs)}, and no PR of it was found, so whether it opened one cannot be told"
    elif back and not live and (it["kind"] != "task" or "task" in it):
        kept = "" if tidy(pol, it, True) else f", and its old worktree {it['worktree']} keeps its uncommitted or unpushed work"
        if it["kind"] == "task":
            q["tasks"][key] = {**it["task"], "preapproved": it["preapproved"]}
        q["verdicts"] = {k: v for k, v in q["verdicts"].items() if held_pr(q, int(k))}
        pushed = f" after it pushed {', '.join(refs)}" if refs else ""
        return "tasks", f"{ended_as} with no PR{pushed}, so it goes back to tasks to run again{kept}"
    if not tidy(pol, it, True):
        return wait(f"{why}, and its worktree has uncommitted or unpushed work")
    q["done"].append(key)
    head, v = live["headRefOid"] if live else None, q["verdicts"].get(str(it["pr"]), {})
    row = f"{now()} ended {key} pr={it['pr'] or '-'} head={head or '-'} verdict={v['result'] if head and v.get('head') == head else '-'} {why}"
    q.setdefault("ended", []).append(row.replace("\r", " ").replace("\n", " ") + "\n")
    q["verdicts"] = {k: v for k, v in q["verdicts"].items() if held_pr(q, int(k))}
    return "done", why


def unverified(pol, q, it):
    try:
        live = its_pr(pol, q, it)
    except Refused as e:
        return f"Whether your PR's head has a verdict cannot be told ({e}), so your turn is not over. Try again."
    if live and live["state"] == "OPEN" and live["headRefOid"] != q["verdicts"].get(str(it["pr"]), {}).get("head"):
        return (f"PR {it['pr']} has no verdict for its current head, so your turn is not over. Have the verifier check that head and "
                "wait for its verdict inside this turn.")
    return None


def release(pol, ident=None, hook=False, died=False):
    thread = os.environ.get(THREAD_VAR)

    def go(q):
        key = ident or next((k for k, v in q["running"].items() if thread and v["thread"] == thread), None)
        was = "running" if key in q["running"] else "waiting" if key in q["waiting"] else None
        if not was:
            if ident:
                raise Refused(f"{ident} is not running or waiting on its PR")
            return {"released": None}, False
        held_back = unverified(pol, q, q["running"][key]) if hook and was == "running" else None
        if held_back:
            return Unfinished(held_back), False
        it = q[was][key]
        state, why = outcome(pol, q, key, died=died)
        said = None if state == was else "wake: " + wake(pol, "done" if state == "done" else "blocked", f"{key}: {why}", it["pr"], it["thread"])
        return {"released": key, "state": state, "why": why, "wake": said}, True
    out = locked(pol, go)
    if isinstance(out, Unfinished):
        raise out
    return out


def unresume(q):
    for key, row in sorted(q["tasks"].items()):
        if row.get("pr") and key not in q["running"]:
            q["waiting"][key] = {**{k: row[k] for k in ("kind", "n", "pr", "preapproved", "title", "priority", "at")}, "readonly": False,
                                 "thread": None, "worktree": None, "resumed": True,
                                 **{k: row[k] for k in ("dropped", "seen", "queued", "kept") if k in row},
                                 **({"task": row} if row["kind"] == "task" else {})}
            del q["tasks"][key]


def settle(pol, q):
    cleared, kept = [], []
    for key in sorted(q["waiting"]):
        state, why = outcome(pol, q, key, merge=True)
        (kept if state == "waiting" else cleared).append({"id": key, "why": why})
    return cleared, kept


def plugin(head, out):
    known, home = os.path.expanduser("~/.claude/plugins/installed_plugins.json"), os.path.expanduser("~/.claude/plugins/known_marketplaces.json")
    installed = read_text(known) or ""
    if not shutil.which("claude") or '"zstack@zstack"' not in installed:
        return
    if f'"installLocation": "{PKG}"' not in (read_text(home) or "") or f'"gitCommitSha": "{head}"' in installed:
        return
    subprocess.run(["claude", "plugin", "marketplace", "update", "zstack"], capture_output=True)
    ok = subprocess.run(["claude", "plugin", "update", "zstack@zstack"], capture_output=True).returncode == 0
    print("zstack: plugin updated; new threads load it" if ok else
          "zstack: the plugin did not update, so threads still run the old hooks: run claude plugin update zstack@zstack", file=out)


def update(out=sys.stdout):
    def g(*args):
        p = subprocess.run(["git", "-C", PKG, *args], capture_output=True, text=True, env=NO_GIT_ENV)
        return p.returncode, p.stdout.strip()

    def die(why):
        raise Refused(f"{why}; not launching on an old tree")
    if g("rev-parse", "--show-toplevel")[0]:
        die(f"{PKG} is not a git checkout")
    rc, branch = g("symbolic-ref", "--short", "-q", "HEAD")
    if rc:
        die(f"{PKG} has a detached HEAD at {g('rev-parse', '--short', 'HEAD')[1]}; check out main")
    if branch != "main":
        die(f"{PKG} is on branch {branch}, not main")
    if dirty := g("status", "--porcelain", "--untracked-files=no")[1]:
        die(f"{PKG} has uncommitted changes: {' '.join(dirty.splitlines()[:3])}")
    if g("fetch", "--quiet", "origin", "refs/heads/main:refs/remotes/origin/main")[0]:
        die(f"git fetch origin main failed in {PKG} (network or remote?)")
    old, new = g("rev-parse", "HEAD")[1], g("rev-parse", "refs/remotes/origin/main")[1]
    if old == new:
        print(f"zstack: already at {old}", file=out)
    elif g("merge-base", "--is-ancestor", old, new)[0]:
        die(f"{PKG} main ({old}) has commits not on origin/main ({new}); not a fast-forward")
    elif g("merge", "--ff-only", "--quiet", new)[0]:
        die(f"git merge --ff-only origin/main failed in {PKG}")
    else:
        print(f"zstack: {old} -> {new}", file=out)
    plugin(new, out)
    return 0


ZERO = "0" * 40
TOOLS = r"claude|anthropic|openai|codex|chatgpt|gpt-|copilot|cursor|gemini|grok|devin|aider|\[bot\]"


def status_paths(name_status):
    return [p for line in name_status.splitlines() for p in line.split("\t")[1:]]


def lint(head, cwd=None):
    _, ident = git("var", "GIT_COMMITTER_IDENT", cwd=cwd)
    me = ident[ident.find("<") + 1:ident.find(">")]
    rc, log = git("log", "--no-merges", "--format=%H%x00%ce%x00%B%x01", head, "--not", "refs/remotes/origin/main", cwd=cwd)
    out = []
    for entry in log.split("\x01") if rc == 0 else []:
        c, _, rest = entry.strip().partition("\x00")
        who, _, body = rest.partition("\x00")
        if c and who == me and re.search(rf"^co-authored-by:.*({TOOLS})", body, re.I | re.M):
            out.append(f"commit {c[:12]} carries a Co-authored-by trailer for a model, agent or tool (the repo invariant text)")
    return out[:8]


BUILT = re.compile(r"(^|/)(dist|bundle)/|\.(min|bundle)\.[cm]?js$")
DOCS = re.compile(r"\.(md|mdx|markdown)$", re.I)
DOC_ROW = re.compile(r"\s*\|")
DOC_COMMENT = re.compile(r"\s*(///|//!|/\*\*|/\*!|\*(\s|$))")
LONG_LINE = 1000


def one_line(text):
    return text.splitlines() == [text]


def unmatched(lines):
    same = Counter(n for s, n, _ in lines if s == "-") & Counter(n for s, n, _ in lines if s == "+")
    left = {"-": Counter(same), "+": Counter(same)}
    out = []
    for s, n, raw in lines:
        if left[s][n]:
            left[s][n] -= 1
        else:
            out.append((n, raw))
    return out


class Patent:
    def __init__(self, pol):
        self.pol, pt = pol, pol.get("patent") or {"held_words": [], "skip_lockfiles": ["-"], "private_repo": ""}
        self.on = "patent" in pol
        self.held = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, pt["held_words"])) + ")", re.I)
        self.lockfile = re.compile(r"(^|/)(" + "|".join(map(re.escape, pt["skip_lockfiles"])) + ")$")
        self.public, self.staging = pol["merge"]["repo"], pt["private_repo"]
        self.words = " or ".join(filter(None, [", ".join(pt["held_words"][:-1]), *pt["held_words"][-1:]]))

    def is_staging(self, url):
        return bool(re.search(rf"github\.com[:/]{re.escape(self.staging)}(\.git)?/?$", url or ""))

    def held_hits(self, paths, diff, public=lambda text: False):
        hits = [p for p in paths if not self.lockfile.search(p) and not BUILT.search(p) and self.held.search(p)]
        skip, docs, cols, hunk, lines = False, False, 1, False, []
        fulls = (diff or "").split("\n")
        clean = all(one_line(full) for full in fulls if full)
        for line in [line for full in fulls for line in full.replace("\r\n", "\n").replace("\r", "\n").splitlines()] + ["@@"]:
            if line.startswith(("diff --git ", "diff --cc ", "diff --combined ", "@@")):
                hits += [raw for n, raw in unmatched(lines) if not (clean and public(n))]
                lines = []
            if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
                path = line.rsplit(" b/", 1)[-1] if line.startswith("diff --git ") else line.split(" ", 2)[-1]
                skip, docs, cols, hunk = bool(self.lockfile.search(path) or BUILT.search(path)), bool(DOCS.search(path)), 1, False
                continue
            if line.startswith("@@"):
                cols, hunk = len(line) - len(line.lstrip("@")) - 1, True
                continue
            if not hunk and line.startswith(("+++ ", "--- ")):
                continue
            text = line[cols:]
            if skip or line[:cols] not in ("+" * cols, "-" * cols) or len(text) > LONG_LINE:
                continue
            if (DOC_ROW if docs else DOC_COMMENT).match(text) or not self.held.search(text):
                continue
            lines.append((line[0], " ".join(text.split()), line[:100]))
        return list(dict.fromkeys(hits))[:5]

    def choose_remote(self, url, hits, staging_ok):
        if not hits or self.is_staging(url):
            return "push"
        return "staging" if staging_ok() else "hold"

    def diff_hits(self, head, cwd=None):
        rc, base = git("merge-base", head, "refs/remotes/origin/main", cwd=cwd)
        base = base.strip() if rc == 0 and base.strip() else None
        if not base:
            return ["no merge base with origin/main (diff unreadable)"]
        rc1, paths = git("diff", "--name-status", "--find-renames", "--no-color", base, head, cwd=cwd)
        rc2, diff = git("diff", "-U0", "--find-renames", "--no-color", base, head, cwd=cwd, raw=True)
        rc3, revs = git("rev-list", "--reverse", head, "--not", "refs/remotes/origin/main", cwd=cwd)
        if rc1 or rc2 or rc3:
            return ["diff unreadable"]
        public = functools.cache(lambda text: self.in_tree(text, base, cwd))
        hits = self.held_hits(status_paths(paths), diff, public)
        for c in revs.split():
            rc1, paths = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "--name-status", "--find-renames", c, cwd=cwd)
            rc2, diff = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "-U0", "--find-renames", "--no-color", c, cwd=cwd, raw=True)
            if rc1 or rc2:
                return hits + [f"commit {c[:12]} diff unreadable"]
            hits += [f"{c[:12]}: {h}" for h in self.held_hits(status_paths(paths), diff, public)]
        return list(dict.fromkeys(hits))[:5]

    @staticmethod
    def in_tree(text, tree, cwd=None):
        try:
            rc, out = git("--no-replace-objects", "grep", "-h", "-I", "-F", "--no-line-number", "--no-column", "--no-color",
                          "--no-recurse-submodules", "--no-textconv", "-e", max(text.split(), key=len), tree, cwd=cwd, raw=True)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return False
        return rc == 0 and text in {" ".join(x.split()) for x in out.split("\n") if one_line(x)}

    def staging_push_ok(self):
        rc, out, _ = gh("api", f"repos/{self.staging}", "--jq", ".permissions.push")
        return rc == 0 and out.strip() == "true"

    def wake_hold(self, branch, head, hits):
        ident, log = f"{self.public.split('/')[1]}:{branch}", os.path.join(root(self.pol), "events.log")
        mark = f" patent-held {ident} at {head} "
        sent = ("webhook sent", "not sent: dry run")
        if any(mark in line and line.split(mark, 1)[1].startswith(sent) for line in (read_text(log) or "").splitlines()):
            return False
        me, owner = self.pol["people"]["me"], self.pol["people"]["owner"]
        reason = (f"held for the private repo: the diff touches {self.words} ({'; '.join(hits)[:200]}), "
                  f"so it was not pushed to public {self.public}. {self.staging} is not pushable as {me}, so the branch "
                  f"stays local. {owner}: grant push on {self.staging} or say where it goes.")
        status = wake(self.pol, "blocked", f"{ident} {reason}")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a") as f:
            f.write(f"{now()}{mark}{status}: {reason}\n".replace("\r", " "))
        return status.startswith(sent)

    def main(self, argv, stdin, cwd=None, staging_ok=None, out=sys.stderr):
        url, owner, refuse, pushed = argv[1] if len(argv) > 1 else "", self.pol["people"]["owner"], 0, []
        for line in stdin.read().split("\n"):
            f = line.rsplit(" ", 3)
            if len(f) != 4 or f[1] == ZERO:
                continue
            _, head, remote_ref, _ = f
            branch = remote_ref[len("refs/heads/"):] if remote_ref.startswith("refs/heads/") else remote_ref
            if problems := lint(head, cwd):
                refuse = 1
                out.write(f"push refused: {branch} breaks the repo's invariants: " + "; ".join(problems)
                          + ". Fix the commits and push again; do not skip the hook.\n")
                continue
            hits = self.diff_hits(head, cwd) if self.on else []
            act = self.choose_remote(url, hits, staging_ok or self.staging_push_ok)
            if act == "push":
                pushed.append((remote_ref, head))
                continue
            refuse = 1
            if act == "staging" and git("push", f"https://github.com/{self.staging}", f"{head}:refs/heads/{branch}", cwd=cwd)[0] == 0:
                log_push(self.pol, f"https://github.com/{self.staging}", f"refs/heads/{branch}", head, cwd)
                out.write(f"patent hold: {branch} touches {self.words}, so it was pushed to the private "
                          f"repo {self.staging}, not to {url}. Open or update its PR there: gh pr create -R {self.staging} "
                          f"--head {branch}. Never push it to {self.public}.\n")
                continue
            log_push(self.pol, url, remote_ref, head, cwd, refused=True)
            woke = f"{owner} has been woken" if self.wake_hold(branch, head, hits) else f"{owner} was woken earlier, or the wake failed (events.log)"
            out.write(f"patent hold: {branch} touches {self.words} and must not go to public "
                      f"{self.public}. It stays local, and {woke}. Do not retry the push, push "
                      f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
        for ref, head in [] if refuse else pushed:
            log_push(self.pol, url, ref, head, cwd)
        return refuse


def pre_push(argv, stdin):
    print(f"zstack {sha()}", file=sys.stderr)
    try:
        pol = load()
    except Refused as e:
        print(f"patent hold: the config is unreadable ({e}), so nothing is pushed until it is fixed. Do not retry "
              "the push, push elsewhere or skip the hook; say so in your report.", file=sys.stderr)
        return 1
    return Patent(pol).main(argv, stdin)


def queue_command(pol, argv, stdin):
    if argv[:1] == ["add"] and len(argv) >= 2:
        rest = [a for a in argv[2:] if a != "--preapproved"]
        prio = not rest or (len(rest) == 2 and rest[0] == "--priority" and re.fullmatch(r"-?\d+", rest[1]))
        if argv[2:].count("--preapproved") <= 1 and prio:
            return add(pol, argv[1], stdin.read(), int(rest[1]) if rest else 0, "--preapproved" in argv)
    if argv == ["pass"]:
        return run_pass(pol)
    if len(argv) == 2 and argv[0] == "approve":
        return approve(pol, argv[1])
    if len(argv) == 5 and argv[0] == "verified" and argv[2].isdigit() and re.fullmatch(SHA, argv[3]) and argv[4] in ("auto", "review", "fail"):
        return verified(pol, argv[1], int(argv[2]), argv[3], argv[4])
    if len(argv) == 3 and argv[0] == "gate" and argv[1].isdigit() and re.fullmatch(SHA, argv[2]):
        return gate(pol, int(argv[1]), argv[2])
    if argv == ["release", "--hook"]:
        return release(pol, hook=True)
    if argv == ["release", "--died"]:
        return release(pol, died=True)
    if len(argv) == 2 and argv[0] == "complete":
        return complete(pol, argv[1])
    if argv[:1] == ["release"] and len(argv) <= 2:
        return release(pol, argv[1] if len(argv) == 2 else None)
    if argv == ["list"]:
        return locked(pol, lambda q: (q, False))
    return None


def main(argv, stdin=sys.stdin):
    try:
        if argv == ["update"]:
            return update()
        if argv[:1] == ["pre-push"]:
            return pre_push(argv[1:], stdin)
        done = config_command(argv)
        if done is not None:
            return done
        out = queue_command(load(), argv, stdin)
        if out is not None:
            return print(json.dumps(out, indent=1)) or 0
    except Unfinished as e:
        print(e, file=sys.stderr)
        return 3
    except Refused as e:
        print(f"queue: {e}", file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

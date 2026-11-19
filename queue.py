#!/usr/bin/env python3
import fcntl
import json
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
from datetime import datetime, timezone

PKG = os.path.dirname(os.path.realpath(__file__))
USAGE = (
    "The queue:\n"
    "  queue.py pickup [--dry]          read the repo, queue what this machine takes, and clear what has finished\n"
    "  queue.py add <id> [--priority N] [--preapproved]\n"
    "                                   queue a task from the orchestrator; its text on stdin\n"
    "  queue.py next                    what to launch now and the running threads, as JSON\n"
    "  queue.py claim <id> <thread> <worktree>\n"
    "                                   the thread just launched for the next item, and the worktree it works in\n"
    "  queue.py verified <id> <pr> <head> auto|review|fail\n"
    "                                   the verifier's verdict on that head: its thread may merge it, a person must\n"
    "                                   review it, or its thread fixes what was found\n"
    "  queue.py gate <pr> <head>        may that head of that PR merge now; exits 1 with the reason when it may not\n"
    "  queue.py release [<id>|--hook]   the thread's turn ended: free its slot and tell the orchestrator the outcome.\n"
    "                                   A finished item leaves the queue; one whose PR waits for the owner is kept as\n"
    "                                   review. With no id, the item of the thread that runs it. With --hook, exit 3\n"
    "                                   and free nothing while its open PR's head has no verdict, so the turn goes on\n"
    "  queue.py list                    the queue as it stands\n"
    "The config:\n"
    "  queue.py check                   parse and validate the machine file, each repo it works on and the rules\n"
    '  queue.py seat <role>[:<n>]       "<provider> <model>" for a role from [models]; a role with several seats needs :<n>\n'
    "  queue.py seats                   every role and seat\n"
    "  queue.py get <key>               one config value by dotted key (`merge.repo`): a string or number as is, else JSON\n"
    "  queue.py dump                    the merged config as JSON\n"
    "  queue.py rules [--thread] [<id>...]\n"
    "                                   the Hard rules as markdown; --thread leaves out the orchestrator's; ids name single rules\n"
    "The holds file:\n"
    "  queue.py holds                   the holds file as this program reads it, as JSON\n"
    "  queue.py hold add '<kind> | <target> | <rules> | ... | <source>'\n"
    "  queue.py hold retire '<row as it stands in the file>' '<source>'\n"
    "  queue.py hold replace '<row as it stands in the file>' '<new row>'\n"
    "The machine:\n"
    "  queue.py update                  bring this checkout to origin/main, and the installed plugin with it\n"
    "  queue.py pre-push <remote> <url> the clone's pre-push hook: the patent hold and the commit rules"
)
try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, os.path.join(PKG, "vendor"))
    import tomli as tomllib
CONFIG = os.path.abspath(os.environ.get("ZSTACK_CONFIG") or os.path.join(PKG, "config"))
RULES = os.path.join(PKG, "config", "rules.toml")
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


SEAT = (seats, "a {provider, model} table or a non-empty list of them")
PATHISH = (lambda v: text(v) and v.startswith(("/", "~/")), "a path from / or ~/")
MACHINE = {
    "models": {role: SEAT for role in ("coding", "orchestrator", "explorer", "designer", "verifier")},
    "run": {"idle_min": (pos_int, "a positive integer"), "threads": (pos_int, "a positive integer")},
    "wake": {"url": (lambda v: text(v) and v.startswith("https://"), "an https URL"), "key_file": PATHISH},
}
PLACE = {"clone": PATHISH, "root": PATHISH, "labels": (names, "a list of distinct label names"),
         "unlabelled": (lambda v: type(v) is bool, "true or false")}
REPO = {
    "people": {"me": (text, "a login"), "owner": (text, "a name")},
    "patent": {
        "held_words": (lambda v: words(v) and bool(v), "a non-empty list of distinct lowercase words"),
        "skip_lockfiles": (lambda v: names(v) and bool(v) and not any("/" in x for x in v), "a non-empty list of distinct file names"),
        "private_repo": (repo, "owner/name"),
    },
    "merge": {
        "gate_check": (text, "a check name"),
        "review_label": (text, "a label name"),
        "preapproved_labels": (names, "a list of distinct label names"),
    },
    "issues": {
        "fixes": line_with("{issue}"),
        "refs": line_with("{issue}"),
        "salvaged_from": (lambda v: text(v) and v.endswith(" #{pr}") and "{" not in v[:-5], "one line ending in ` #{pr}`, with no other placeholder"),
    },
    "review": {"outside": (lambda v: v in ("salvage", "request-changes"), "salvage or request-changes")},
}


def sections(where, data, schema, extra=()):
    if type(data.get("schema")) is not int or data["schema"] != 1:
        raise Refused(f"{where}: schema must be 1, not {data.get('schema')!r}")
    for key in data.keys() - schema.keys() - {"schema", *extra}:
        raise Refused(f"unknown key {key}")
    for name, spec in schema.items():
        sec = data.get(name)
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
    sections(name, data, REPO)
    if name.lower() == data["patent"]["private_repo"].lower():
        raise Refused("patent.private_repo must differ from merge.repo")
    if data["merge"]["review_label"] in data["merge"]["preapproved_labels"]:
        raise Refused("merge.review_label cannot also be one of merge.preapproved_labels")
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
    return {"schema": 1, **{k: machine[k] for k in MACHINE}, **{k: project[k] for k in REPO},
            "merge": {"repo": name, **project["merge"]}, "repos": known,
            "host": {"clone": place["clone"], "root": root, "holds": root + "/hold-prs.txt",
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
        if isinstance(v, list) and all(type(x) in (str, int) for x in v) and v and how:
            v = [str(x) for x in v]
            return (", ".join(v[:-1]) + f" {how} " + v[-1] if len(v) > 1 else v[0]) if how in ("and", "or") else how.join(v)
        if type(v) not in (str, int) or how:
            why = "a list and needs a joiner" if isinstance(v, list) and not how else "not one value"
            raise Refused(f"rules: {where} names {m.group(0)}, which is {why}")
        return re.sub(r"\{(\w+)\}", r"<\1>", str(v))
    try:
        out = re.sub(r"\{([a-z_]+(?:\.[a-z_]+)+)(?::([^{}]+))?\}", one, s)
    except Refused as e:
        raise Refused(e.args[0] if e.args[0].startswith("rules:") else f"rules: {where}: {e.args[0]} in the config") from None
    if "{" in out or "}" in out:
        raise Refused(f"rules: {where} has a brace that names no config key")
    return out


def validate_rules(data, pol):
    keys("rules", data, ("schema", "intro", "section"))
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise Refused(f"rules: schema must be 1, not {data['schema']!r}")
    if not text(data["intro"]) or not isinstance(data["section"], list) or not data["section"]:
        raise Refused("rules: intro must be text and section a non-empty list")
    seen = {}
    for n, sec in enumerate(data["section"], 1):
        keys(f"section {n}", sec, ("title", "rule"), ("paragraphs",))
        if not text(sec["title"]) or type(sec.get("paragraphs", False)) is not bool:
            raise Refused(f"rules: section {n} needs a title, and paragraphs is true or false")
        if not isinstance(sec["rule"], list) or not sec["rule"]:
            raise Refused(f"rules: section {sec['title']!r} has no rule")
        for r in sec["rule"]:
            keys(f"{sec['title']} rule", r, ("id", "text"), ("source", "for"))
            if not text(r.get("source", "x")) or r.get("for", "orchestrator") != "orchestrator":
                raise Refused(f"rules: {r.get('id')!r} source must be text, and for is \"orchestrator\" or absent")
            if not (isinstance(r["id"], str) and re.fullmatch(r"[a-z]+(-[a-z]+)*", r["id"])) or not text(r["text"]) or "\n" in r["text"]:
                raise Refused(f"rules: {sec['title']} rule {r.get('id')!r} needs a kebab-case id and one line of text")
            if r["id"] in seen:
                raise Refused(f"rules: two rules have the id {r['id']}")
            seen[r["id"]] = r["text"]
    data["intro"] = fill("intro", data["intro"], pol)
    for sec in data["section"]:
        for r in sec["rule"]:
            r["text"] = fill(r["id"], r["text"], pol)
    return data


def rules(path=RULES, want=None):
    pol, data = load(want), read_toml(path)
    more = os.path.join(CONFIG, "repos", pol["merge"]["repo"], "rules.toml")
    if os.path.isfile(more):
        extra = read_toml(more)
        keys(more, extra, ("schema", "section"))
        data["section"] = [*data.get("section", []), *(extra["section"] if isinstance(extra["section"], list) else [None])]
    return validate_rules(data, pol)


def render(data, ids=(), thread=False):
    if ids:
        by = {r["id"]: r["text"] for s in data["section"] for r in s["rule"]}
        if miss := [i for i in ids if i not in by]:
            raise Refused(f"no rule {', '.join(miss)} in {RULES}")
        return "".join(f"- {by[i]}\n" for i in ids)
    out = ["## Hard rules", "", data["intro"]]
    for s in data["section"]:
        shown = [r for r in s["rule"] if not (thread and r.get("for"))]
        if not shown:
            continue
        out += ["", f"### {s['title']}", ""]
        out += ("\n\n" if s.get("paragraphs") else "\n").join(
            r["text"] if s.get("paragraphs") else f"- {r['text']}" for r in shown).split("\n")
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
            rules(want=name)
        return print(f"queue: {CONFIG} is valid for {', '.join(every)} on {os.path.basename(machine_file())[:-5]}") or 0
    if argv[:1] == ["rules"]:
        return print(render(rules(), [x for x in argv[1:] if x != "--thread"], "--thread" in argv), end="") or 0
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


def sha():
    r = subprocess.run(["git", "-C", PKG, "rev-parse", "HEAD"], capture_output=True, text=True, env=NO_GIT_ENV)
    return r.stdout.strip() or "unknown"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path):
    try:
        with open(os.path.expanduser(path)) as f:
            return f.read()
    except OSError:
        return None


def root(pol):
    return os.path.abspath(os.path.expanduser(pol["host"]["root"]))


def parse_holds(rows):
    h = {"hold": set(), "skip": set(), "review_only": set()}
    for ln, raw in enumerate(rows.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        pr = re.fullmatch(r"#?(\d+)", f[1]) if len(f) > 1 else None
        if f[0] == "hold":
            if len(f) < 4 or not pr:
                raise ValueError(f"holds file line {ln}: hold needs a PR number | rules | note")
            h["hold"].add(int(pr.group(1)))
        elif len(f) < 2 or not f[1] or pr:
            raise ValueError(f"holds file line {ln}: {line[:80]}")
        elif f[0] in ("skip-author", "review-only"):
            h["skip" if f[0] == "skip-author" else "review_only"].add(f[1].lower())
    return h


def holds(pol):
    rows = read_text(pol["host"]["holds"])
    try:
        if rows is None:
            raise ValueError(f"cannot read {pol['host']['holds']}")
        return {**parse_holds(rows), "read": True, "error": None}
    except ValueError as e:
        return {"hold": set(), "skip": set(), "review_only": set(), "read": False, "error": str(e)}


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


def wake(pol, kind, message, pr=None):
    body = {"kind": kind, "id": os.environ.get(THREAD_VAR) or None, "pr": pr, "reason": message[:1000],
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
    return {p["number"] for p in gh_json(errors, "pr", "list", "-R", name, "--state", "open", "--limit", "3000", "--json", "number")}


def takes(labels, host):
    mine, hit = set(host["labels"]), sorted(set(labels) & set(host["all_labels"]))
    return hit[0] in mine if hit else (not mine or host["unlabelled"])


def skip(c, h, prs, items, done, host):
    if not h["read"]:
        return "holds-unreadable"
    if c["id"] in items:
        return items[c["id"]]["state"]
    if c["id"] in done:
        return "done"
    if not takes(c["labels"], host):
        return "other-machine"
    if bot(c["author"]) or c["author"]["login"].lower() in h["skip"] or "hold" in c["labels"]:
        return "held"
    if (c["kind"] != "issue" and c["n"] in h["hold"]) or any(n in h["hold"] for n in c["linked"]):
        return "held"
    if c["kind"] == "issue" and any(n in prs for n in c["linked"]):
        return "pr"
    busy = {n for i in items.values() for n in (i["n"], i["pr"]) if n}
    if c["n"] in busy or any(n in busy for n in c["linked"]):
        return "overlap"
    return None


def task(c, h, pol):
    name, me, iss = pol["merge"]["repo"], pol["people"]["me"], pol["issues"]
    if c["kind"] == "issue":
        return (f"{name}#{c['n']}: {c['title']}\n{c['url']}\n\nKind: fix in {name}, picked from the issues assigned to {me}. "
                f"Read the issue and its thread, fix it with a test, and open a PR whose body says `{iss['fixes'].format(issue=c['n'])}`.")
    salvage = pol["review"]["outside"] == "salvage"
    outside = "salvage a fixable PR as a new PR of ours that credits them, instead of requesting changes" if salvage else \
        "request changes on a fixable PR, and do not rewrite it as a PR of ours"
    teammate = c["author"]["login"].lower() in h["review_only"]
    who = "teammate (review-only: a first review and nothing else)" if teammate else f"outside contributor ({outside})"
    said = (f"{name}#{c['n']} at head {c['head']} by {c['author']['login']}, a {who}: {c['title']}\n{c['url']}\n\nKind: first "
            f"review, requested of {me}. Run playbooks/pull-requests.md Verify. Post nothing under the owner's name: put every "
            "comment for the author under a `drafts:` section of your final report.")
    if teammate or not salvage:
        return said
    frm = iss["salvaged_from"].format(pr=c["n"])
    return (f"{said}\nA salvage PR's body carries `{frm}` and a `{iss['fixes'].format(issue='<n>')}` line for each issue "
            f"#{c['n']} fixes or was opened for, so the forge closes them when the salvage merges. Take those issues from "
            f"#{c['n']}'s own body; an issue it names but says it does not fix gets `{iss['refs'].format(issue='<n>')}` instead.\n"
            f"A salvage keeps {c['author']['login']}'s commits as authored: cherry-pick or rebase their commits, then put our "
            "fixes on top as separate commits. Never re-author their work or squash it into our commit. If none of their "
            "commits survive, add `Co-authored-by: <their name> <their GitHub noreply or commit email>` for them to our "
            f"commit; the body still says `{frm}`.")


def item(kind, n, source, priority, said, title, preapproved=False):
    return {"state": "queued", "kind": kind, "n": n, "source": source, "priority": priority, "preapproved": preapproved,
            "task": said, "title": title, "thread": None, "worktree": None, "pr": None, "verdict": None, "why": None, "at": now()}


def locked(pol, fn):
    base = root(pol)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "queue.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = os.path.join(base, "queue.json")
        rows = read_text(path)
        state = json.loads(rows) if rows else {"items": {}, "done": []}
        if "done" not in state or any("verdict" not in v or v["state"] not in ("queued", "running", "review") for v in state["items"].values()):
            raise Refused(f"{path} was written by an older zstack: move it aside, and the queue starts empty")
        out, changed = fn(state["items"], state["done"])
        if changed:
            with open(path + ".tmp", "w") as f:
                json.dump(state, f, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(path + ".tmp", path)
        return out


def pickup(pol, dry):
    h = holds(pol)
    errors = [] if h["read"] else [h["error"]]
    cands, prs = candidates(errors, pol), open_prs(errors, pol["merge"]["repo"])
    cleared = [] if dry or errors else settle(pol)

    def go(items, done):
        queued, skipped = [], []
        for c in [] if errors else cands:
            why = skip(c, h, prs, items, done, pol["host"])
            if why:
                skipped.append({"id": c["id"], "why": why})
                continue
            priority = 100 if "urgent" in c["labels"] else 0
            pre = c["kind"] == "issue" and bool(set(c["labels"]) & set(pol["merge"]["preapproved_labels"]))
            items[c["id"]] = item(c["kind"], c["n"], "pickup", priority, task(c, h, pol), c["title"], pre)
            queued.append({"id": c["id"], "priority": priority})
        gone = [] if errors else [d for d in done if d not in {c["id"] for c in cands}]
        done[:] = [d for d in done if d not in gone]
        return {"queued": queued, "skipped": skipped, "cleared": cleared, "errors": errors}, bool(queued or gone) and not dry
    return locked(pol, go)


def add(pol, ident, said, priority, preapproved=False):
    if not re.fullmatch(ID, ident) or not said.strip():
        raise Refused("an id of letters, digits, . _ - and a task on stdin")

    def go(items, done):
        if ident in items:
            raise Refused(f"{ident} is already {items[ident]['state']}")
        items[ident] = item("task", None, "orchestrator", priority, said.strip(), said.strip().splitlines()[0][:120], preapproved)
        return items[ident], True
    return locked(pol, go)


def order(items):
    return sorted((k for k, v in items.items() if v["state"] == "queued"), key=lambda k: (-items[k]["priority"], items[k]["at"], k))


def first(items, h, cap):
    if sum(1 for v in items.values() if v["state"] == "running") >= cap:
        return None, "the machine is at its thread cap"
    if not h["read"]:
        return None, f"the holds file is unreadable: {h['error']}"
    free = [k for k in order(items) if items[k]["n"] not in h["hold"]]
    return (free[0], None) if free else (None, "nothing is queued")


BRIEF = """# {id}

{task}

repo: a worktree of `{clone}`

Follow onigiri-mode and its Hard rules, which are already in your context.
No invented limits: a restriction must trace to the owner's words or the Hard rules. If you think one is needed, put it under `questions:` instead of applying it.

When your PR is open and you believe it is done, have the verifier check its head, and wait for its answer inside this turn: do not end your turn while it runs. Put this line in its brief with the PR number and the full head sha filled in: `{env} {queue} verified {id} <pr number> <head sha> auto`, `review` or `fail`. The verifier runs it as its last step, with those variables, since its session may not have yours; you never do. On a fail, fix the findings, push, and have it check the new head.
On `auto`, wait for the PR's checks, then merge that head yourself: `gh pr merge <pr number> -R {repo} --squash --match-head-commit <head sha>`. The guard lets that command through only for the head the verifier passed, with the checks green and no hold; when it refuses, read its reason, and do not try another way. On `review`, or when you think the change needs a person whatever the verifier says, do not merge: say why in your report.
When your turn ends, the queue frees your slot and tells the orchestrator what happened, so finish everything before you stop. A turn that tries to end while your open PR's head has no verdict is sent back to get one.

End with a final report: the PR URLs, then
follow-ups:
questions:
"""  # noqa: E501


def brief(pol, ident, it):
    env = {"ZSTACK_CONFIG": CONFIG, "ZSTACK_MACHINE": os.environ.get("ZSTACK_MACHINE"), "ZSTACK_REPO": pol["merge"]["repo"]}
    return BRIEF.format(id=ident, task=it["task"], clone=pol["host"]["clone"], queue=os.path.join(PKG, "queue.py"), repo=pol["merge"]["repo"],
                        env=" ".join(f"{k}={shlex.quote(v)}" for k, v in env.items() if v))


def next_up(pol):
    h = holds(pol)

    def go(items, done):
        ident, waiting = first(items, h, pol["run"]["threads"])
        launch = {"id": ident, "clone": pol["host"]["clone"], "brief": brief(pol, ident, items[ident])} if ident else None
        return {"launch": launch, "waiting": waiting,
                "running": {k: v["thread"] for k, v in sorted(items.items()) if v["state"] == "running"}}, False
    return locked(pol, go)


def claim(pol, ident, thread, worktree):
    h = holds(pol)

    def go(items, done):
        it = items.get(ident)
        if not it or it["state"] != "queued":
            raise Refused(f"{ident} is {it['state'] if it else 'not in the queue'}, not queued")
        up, waiting = first(items, h, pol["run"]["threads"])
        if up != ident:
            raise Refused(f"{ident} is not next: {waiting or up + ' is'}")
        it.update(state="running", thread=thread, worktree=worktree, at=now())
        return it, True
    return locked(pol, go)


def pr_view(pol, pr):
    rc, out, err = gh("pr", "view", str(pr), "-R", pol["merge"]["repo"], "--json", "state,headRefOid,labels,number,isDraft,statusCheckRollup")
    if rc:
        raise Refused(f"cannot read PR {pr}: {err}")
    return json.loads(out)


def gate_green(pol, live):
    name = pol["merge"]["gate_check"]
    return any((c.get("name") or c.get("context")) == name and (c.get("conclusion") or c.get("state")) == "SUCCESS"
               for c in live.get("statusCheckRollup") or [])


def blocked(pol, it, live, h):
    v = it["verdict"]
    if not v or v["result"] == "fail" or v["head"] != live["headRefOid"]:
        return f"the verifier has not passed head {live['headRefOid']}"
    if not it["preapproved"] and (v["result"] != "auto" or pol["merge"]["review_label"] in [x["name"] for x in live["labels"]]):
        return "it needs a person's review"
    if it["pr"] in h["hold"] or it["n"] in h["hold"] or not h["read"]:
        return "it has a hold row"
    if live.get("isDraft"):
        return "the PR is a draft"
    if not gate_green(pol, live):
        return f"{pol['merge']['gate_check']} has not passed on that head"
    running = any(c.get("status", "COMPLETED") != "COMPLETED" or c.get("state") in ("PENDING", "EXPECTED")
                  for c in live.get("statusCheckRollup") or [])
    return "a check is still running on that head, and a merge now would arm auto-merge" if running else None


def verified(pol, ident, pr, head, result):
    h = holds(pol)

    def go(items, done):
        it = items.get(ident)
        if not it or it["state"] == "queued":
            raise Refused(f"{ident} is {it['state'] if it else 'not in the queue'}, and no thread has run it")
        if it["thread"] and os.environ.get(THREAD_VAR) == it["thread"]:
            raise Refused(f"this is {ident}'s own thread: the verifier it starts records the verdict, never the thread")
        live = pr_view(pol, pr)
        if live["headRefOid"] != head:
            raise Refused(f"PR {pr} is at {live['headRefOid']}, not {head}: the head moved, so check the new one")
        for other in items.values():
            if other["pr"] == pr:
                other["verdict"] = None
        it.update(pr=pr, verdict={"head": head, "result": result}, at=now())
        if result == "fail":
            return {**it, "say": "its thread fixes the findings and has the new head checked"}, True
        why = blocked(pol, it, live, h)
        cmd = f"gh pr merge {pr} -R {pol['merge']['repo']} --squash --match-head-commit {head}"
        return {**it, "say": f"its thread does not merge it: {why}" if why else f"its thread merges it with `{cmd}`"}, True
    return locked(pol, go)


def gate(pol, pr, head):
    h = holds(pol)

    def go(items, done):
        it = next((v for v in items.values() if v["pr"] == pr and v["verdict"]), None)
        live = pr_view(pol, pr)
        if live["headRefOid"] != head:
            raise Refused(f"PR {pr} is at {live['headRefOid']}, not {head}")
        why = blocked(pol, it, live, h) if it else f"the verifier has not passed head {head}"
        if why:
            raise Refused(f"PR {pr} does not merge: {why}")
        return {"merge": pr, "head": head}, False
    return locked(pol, go)


def git(*args, cwd=None):
    p = subprocess.run(["git", *args], capture_output=True, text=True, env=ENV, cwd=cwd, timeout=300)
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


def outcome(pol, items, done, key, h):
    it = items[key]
    try:
        it["pr"] = it["pr"] or branch_pr(pol, it)
        live = pr_view(pol, it["pr"]) if it["pr"] else None
        other = branch_pr(pol, it) if live and live["state"] != "OPEN" else None
        if other and other != it["pr"]:
            it.update(pr=other, verdict=None)
            live = pr_view(pol, other)
    except Refused as e:
        it.update(state="review", why=str(e), at=now())
        return "blocked"
    if live and live["state"] == "OPEN":
        why = f"PR {it['pr']} waits for the owner: {blocked(pol, it, live, h) or 'its thread did not merge it'}"
        it.update(state="review", why=why, at=now())
        tidy(pol, it, False)
        return "blocked"
    why = f"PR {it['pr']} {live['state'].lower()}" if live else "nothing to merge"
    if not tidy(pol, it, True):
        it.update(state="review", why=f"{why}, and its worktree has uncommitted or unpushed work", at=now())
        return "blocked"
    it["why"] = why
    del items[key]
    done.append(key)
    return "done"


def tell(pol, kind, ident, it):
    return "wake: " + kind + " " + wake(pol, kind, ident + ": " + it["why"], it["pr"])


def unverified(pol, it):
    try:
        pr = it["pr"] or branch_pr(pol, it)
        live = pr_view(pol, pr) if pr else None
    except Refused:
        return None
    return pr if live and live["state"] == "OPEN" and live["headRefOid"] != (it["verdict"] or {}).get("head") else None


def release(pol, ident=None, hook=False):
    h = holds(pol)
    thread = os.environ.get(THREAD_VAR)

    def go(items, done):
        key = ident or next((k for k, v in items.items() if thread and v["thread"] == thread and v["state"] != "queued"), None)
        it = items.get(key)
        if not it or it["state"] == "queued":
            if ident:
                raise Refused(f"{ident} is {it['state'] if it else 'not in the queue'}, not running")
            return {"released": None}, False
        pr = unverified(pol, it) if hook and it["state"] == "running" else None
        if pr:
            return Unfinished(f"PR {pr} has no verdict for its current head, so your turn is not over. Have the verifier check that head and "
                              "wait for its verdict inside this turn."), False
        was = (it["state"], it["why"])
        kind = outcome(pol, items, done, key, h)
        state = items[key]["state"] if key in items else "done"
        return {"released": key, **it, "state": state, "wake": tell(pol, kind, key, it) if was != (state, it["why"]) else None}, True
    out = locked(pol, go)
    if isinstance(out, Unfinished):
        raise out
    return out


def settle(pol):
    h = holds(pol)

    def go(items, done):
        moved = []
        for ident, it in sorted(items.items()):
            if it["state"] == "review":
                was = it["why"]
                outcome(pol, items, done, ident, h)
                if ident not in items:
                    moved.append({"id": ident, "why": it["why"]})
                elif was != it["why"]:
                    tell(pol, "blocked", ident, it)
        return moved, True
    return locked(pol, go)


MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
WHEN = re.compile(rf"\b(\d{{1,2}} ({MONTHS})|({MONTHS}) \d{{1,2}}|\d{{4}}-\d{{2}}-\d{{2}})[ ,T]+\d{{1,2}}:\d{{2}}\b")


def source_ok(owner, src):
    return bool(re.search(rf"\b{re.escape(owner)}\b", src)) and bool(WHEN.search(src))


def check_row(owner, row):
    if row.splitlines() != [row]:
        return "one row only: it has a line break"
    f = [x.strip() for x in row.split("|")]
    if len(f) < 3 or not all(f[:2]) or row.lstrip().startswith("#"):
        return "a row is `kind | target | rules | ... | source`"
    if not source_ok(owner, f[-1]):
        return f"the last column must name {owner}'s call and its time (e.g. `{owner} 11 Nov 14:56 SGT`), not {f[-1]!r}"
    return None


def put(path, rows):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".hold-prs.txt.")
    with os.fdopen(fd, "w") as f:
        f.write(rows)
        f.flush()
        os.fsync(f.fileno())
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def hold_edit(path, change):
    if not os.path.isfile(path):
        raise Refused(f"{path} does not exist; rows are written only to the merge host's holds file")
    with open(os.path.join(os.path.dirname(path), ".hold-row.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = read_text(path)
        try:
            parse_holds(old)
        except ValueError as e:
            raise Refused(f"the holds file is already unreadable ({e}); not changing it") from None
        new, said = change(old)
        if new is None:
            return said
        try:
            parse_holds(new)
        except ValueError as e:
            raise Refused(f"the holds parser rejects the result ({e}); nothing written") from None
        backup = f"{path}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        shutil.copy2(path, backup)
        put(path, new)
        return f"{said}\nbackup: {backup}"


def hold_add(owner, path, row):
    if why := check_row(owner, row):
        raise Refused(why)
    row = row.strip()

    def change(old):
        if row in (x.strip() for x in old.splitlines()):
            return None, f"already there: {row}"
        return old + ("" if not old or old.endswith("\n") else "\n") + row + "\n", f"added: {row}"
    return hold_edit(path, change)


def hold_retire(owner, path, old_row, source, new_row=None):
    if old_row.splitlines() != [old_row] or not old_row.strip() or old_row.lstrip().startswith("#"):
        raise Refused("name the row to retire exactly as it stands in the file, one line")
    old_row = old_row.strip()
    if source.splitlines() != [source] or not source_ok(owner, source):
        raise Refused(f"the source must name {owner}'s call and its time (e.g. `{owner} 11 Nov 14:56 SGT`), not {source!r}")
    if new_row is not None:
        if why := check_row(owner, new_row):
            raise Refused(why)
        new_row = new_row.strip()
    tomb = f"# retired {source.strip()}: {old_row}"

    def change(rows):
        lines = rows.splitlines()
        hits = [i for i, x in enumerate(lines) if x.strip() == old_row]
        if not hits:
            if tomb in (x.strip() for x in lines) and (new_row is None or new_row in (x.strip() for x in lines)):
                return None, f"already retired: {old_row}"
            raise Refused(f"no row in the file is exactly {old_row!r}")
        if len(hits) > 1:
            raise Refused(f"{len(hits)} rows are exactly {old_row!r}; retire them by hand")
        if new_row is not None and new_row in (x.strip() for x in lines):
            raise Refused(f"the new row is already in the file: {new_row!r}")
        lines[hits[0]:hits[0] + 1] = [tomb] + ([new_row] if new_row is not None else [])
        return "\n".join(lines) + "\n", f"retired: {old_row}" + (f"\nadded: {new_row}" if new_row is not None else "")
    return hold_edit(path, change)


def hold_command(pol, argv):
    owner, path = pol["people"]["owner"], os.path.realpath(os.path.expanduser(pol["host"]["holds"]))
    if len(argv) == 2 and argv[0] == "add":
        return hold_add(owner, path, argv[1])
    if len(argv) == 3 and argv[0] == "retire":
        return hold_retire(owner, path, argv[1], argv[2])
    if len(argv) == 3 and argv[0] == "replace":
        return hold_retire(owner, path, argv[1], [x.strip() for x in argv[2].split("|")][-1], argv[2])
    return None


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
SPDX = "SPDX-License-Identifier"
TOOLS = r"claude|anthropic|openai|codex|chatgpt|gpt-|copilot|cursor|gemini|grok|devin|aider|\[bot\]"


def status_paths(name_status):
    return [p for line in name_status.splitlines() for p in line.split("\t")[1:]]


def has_spdx(head, path, cwd):
    rc, said = git("show", f"{head}:{path}", cwd=cwd)
    return rc == 0 and SPDX in "\n".join(said.splitlines()[:10])


def lint(head, cwd=None):
    rc, base = git("merge-base", head, "refs/remotes/origin/main", cwd=cwd)
    base = base.strip()
    rc1, status = git("diff", "--name-status", "--no-renames", "--no-color", base, head, cwd=cwd) if rc == 0 and base else (1, "")
    if rc1:
        return []
    out, added = [], []
    for row in status.splitlines():
        kind, _, path = row.partition("\t")
        if kind.startswith("D"):
            continue
        parts = path.split("/")
        if parts[-1] == ".onigiri.json":
            out.append(f"{path} is onigiri config and is never committed (Hard rules, scope-no-config)")
        if "target" in parts[:-1]:
            out.append(f"{path} is build output under target/ (Hard rules, checks-target)")
        if kind.startswith("A"):
            added.append(path)
    for path in added:
        folder, ext = os.path.dirname(path), os.path.splitext(path)[1]
        rc2, found = git("ls-tree", "--name-only", base, (folder + "/") if folder else ".", cwd=cwd)
        near = [n for n in found.splitlines() if ext and n.endswith(ext) and n != path][:20]
        if rc2 == 0 and not has_spdx(head, path, cwd) and any(has_spdx(base, n, cwd) for n in near):
            out.append(f"{path} is new and lacks the SPDX header its neighbours carry (Hard rules, text-spdx)")
    _, ident = git("var", "GIT_COMMITTER_IDENT", cwd=cwd)
    me = ident[ident.find("<") + 1:ident.find(">")]
    rc3, log = git("log", "--no-merges", "--format=%H%x00%ce%x00%B%x01", head, "--not", "refs/remotes/origin/main", cwd=cwd)
    for entry in log.split("\x01") if rc3 == 0 else []:
        c, _, rest = entry.strip().partition("\x00")
        who, _, body = rest.partition("\x00")
        if c and who == me and re.search(rf"^co-authored-by:.*({TOOLS})", body, re.I | re.M):
            out.append(f"commit {c[:12]} carries a Co-authored-by trailer for a model, agent or tool (Hard rules, text-no-trailers)")
    return list(dict.fromkeys(out))[:8]


class Patent:
    def __init__(self, pol):
        self.pol, pt = pol, pol["patent"]
        self.held = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, pt["held_words"])) + ")", re.I)
        self.lockfile = re.compile(r"(^|/)(" + "|".join(map(re.escape, pt["skip_lockfiles"])) + ")$")
        self.public, self.staging = pol["merge"]["repo"], pt["private_repo"]
        self.words = ", ".join(pt["held_words"][:-1]) + " or " + pt["held_words"][-1] if len(pt["held_words"]) > 1 else pt["held_words"][0]

    def is_staging(self, url):
        return bool(re.search(rf"github\.com[:/]{re.escape(self.staging)}(\.git)?/?$", url or ""))

    def held_hits(self, paths, diff):
        hits = [p for p in paths if not self.lockfile.search(p) and self.held.search(p)]
        lock, cols, hunk = False, 1, False
        for line in (diff or "").splitlines():
            if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
                path = line.rsplit(" b/", 1)[-1] if line.startswith("diff --git ") else line.split(" ", 2)[-1]
                lock, cols, hunk = bool(self.lockfile.search(path)), 1, False
                continue
            if line.startswith("@@"):
                cols, hunk = len(line) - len(line.lstrip("@")) - 1, True
                continue
            if not hunk and line.startswith(("+++ ", "--- ")):
                continue
            if not lock and line[:cols] in ("+" * cols, "-" * cols) and self.held.search(line[cols:]):
                hits.append(line[:100])
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
        rc2, diff = git("diff", "-U0", "--find-renames", "--no-color", base, head, cwd=cwd)
        rc3, revs = git("rev-list", "--reverse", head, "--not", "refs/remotes/origin/main", cwd=cwd)
        if rc1 or rc2 or rc3:
            return ["diff unreadable"]
        hits = self.held_hits(status_paths(paths), diff)
        for c in revs.split():
            rc1, paths = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "--name-status", "--find-renames", c, cwd=cwd)
            rc2, diff = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "-U0", "--find-renames", "--no-color", c, cwd=cwd)
            if rc1 or rc2:
                return hits + [f"commit {c[:12]} diff unreadable"]
            hits += [f"{c[:12]}: {h}" for h in self.held_hits(status_paths(paths), diff)]
        return list(dict.fromkeys(hits))[:5]

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
        url, owner, refuse = argv[1] if len(argv) > 1 else "", self.pol["people"]["owner"], 0
        for line in stdin.read().splitlines():
            f = line.split()
            if len(f) != 4 or f[1] == ZERO:
                continue
            _, head, remote_ref, _ = f
            branch = remote_ref[len("refs/heads/"):] if remote_ref.startswith("refs/heads/") else remote_ref
            if problems := lint(head, cwd):
                refuse = 1
                out.write(f"push refused: {branch} breaks the Hard rules: " + "; ".join(problems)
                          + ". Fix the commits and push again; do not skip the hook.\n")
                continue
            hits = self.diff_hits(head, cwd)
            act = self.choose_remote(url, hits, staging_ok or self.staging_push_ok)
            if act == "push":
                continue
            refuse = 1
            if act == "staging" and git("push", f"https://github.com/{self.staging}", f"{head}:refs/heads/{branch}", cwd=cwd)[0] == 0:
                out.write(f"patent hold: {branch} touches {self.words}, so it was pushed to the private "
                          f"repo {self.staging}, not to {url}. Open or update its PR there: gh pr create -R {self.staging} "
                          f"--head {branch}. Never push it to {self.public}.\n")
                continue
            woke = f"{owner} has been woken" if self.wake_hold(branch, head, hits) else f"{owner} was woken earlier, or the wake failed (events.log)"
            out.write(f"patent hold: {branch} touches {self.words} and must not go to public "
                      f"{self.public}. It stays local, and {woke}. Do not retry the push, push "
                      f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
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
    if argv in (["pickup"], ["pickup", "--dry"]):
        out = pickup(pol, len(argv) == 2)
        return out, 1 if out["errors"] else 0
    if argv[:1] == ["add"] and len(argv) >= 2:
        rest = [a for a in argv[2:] if a != "--preapproved"]
        prio = not rest or (len(rest) == 2 and rest[0] == "--priority" and re.fullmatch(r"-?\d+", rest[1]))
        if argv[2:].count("--preapproved") <= 1 and prio:
            return add(pol, argv[1], stdin.read(), int(rest[1]) if rest else 0, "--preapproved" in argv), 0
    if argv == ["next"]:
        return next_up(pol), 0
    if len(argv) == 4 and argv[0] == "claim":
        return claim(pol, argv[1], argv[2], argv[3]), 0
    if len(argv) == 5 and argv[0] == "verified" and argv[2].isdigit() and re.fullmatch(SHA, argv[3]) and argv[4] in ("auto", "review", "fail"):
        return verified(pol, argv[1], int(argv[2]), argv[3], argv[4]), 0
    if len(argv) == 3 and argv[0] == "gate" and argv[1].isdigit() and re.fullmatch(SHA, argv[2]):
        return gate(pol, int(argv[1]), argv[2]), 0
    if argv == ["release", "--hook"]:
        return release(pol, hook=True), 0
    if argv[:1] == ["release"] and len(argv) <= 2:
        return release(pol, argv[1] if len(argv) == 2 else None), 0
    if argv == ["list"]:
        return locked(pol, lambda items, done: ({"items": items, "done": done}, False)), 0
    if argv == ["holds"]:
        return {k: sorted(v) if isinstance(v, set) else v for k, v in holds(pol).items()}, 0
    return None, 2


def main(argv, stdin=sys.stdin):
    try:
        if argv == ["update"]:
            return update()
        if argv[:1] == ["pre-push"]:
            return pre_push(argv[1:], stdin)
        done = config_command(argv)
        if done is not None:
            return done
        if argv[:1] == ["hold"]:
            print(f"zstack {sha()}", file=sys.stderr)
            said = hold_command(load(), argv[1:])
            if said is not None:
                return print(said) or 0
        else:
            out, code = queue_command(load(), argv, stdin)
            if out is not None:
                return print(json.dumps(out, indent=1)) or code
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

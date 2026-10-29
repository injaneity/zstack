"""The extracted engine must decide exactly what the tick decided.

Each case is a real PR state, captured with `gh` (see tests/fixtures/README.md), run through both the tick's
merge_decision and the engine's, over every (clef, held, rerun_head, local_pass) the gate can be in. Any
disagreement is a bug in the extraction, not a test to adjust.

The tick is read-only here: imported as a module from ONIGIRI_TICK (default the box's inspection copy).
"""
import gzip
import itertools
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gate"))
import engine

TICK = os.environ.get("ONIGIRI_TICK", "/home/box/agent-data/onigiri/vm-live/tick")
CUA_CFG = engine.load(os.path.join(HERE, "..", "gate", "cua.json"))
ZSTACK_CFG = engine.load(os.path.join(HERE, "..", "gate", "zstack.json"))


def tick_module():
    """The live tick's module. Skipped when the inspection copy is not on this machine."""
    if not os.path.exists(os.path.join(TICK, "tick.py")):
        raise unittest.SkipTest(f"no tick.py at {TICK}")
    if TICK not in sys.path:
        sys.path.insert(0, TICK)
    import tick
    return tick


def fixtures():
    with gzip.open(os.path.join(HERE, "fixtures", "prs.json.gz")) as f:
        return json.load(f)


def both(pr, *, cfg, clef, held, rerun_head, local_pass=frozenset()):
    """(old, new) for one PR in one gate state."""
    tick = tick_module()
    old = tick.merge_decision(pr, repo=cfg.repo, local_pass=set(local_pass), clef=clef, held=held,
                              rerun_head=rerun_head)
    new = engine.merge_decision(pr, cfg=cfg, local_pass=set(local_pass), clef=clef, held=held,
                                rerun_head=rerun_head)
    return old, new


class RealPRParity(unittest.TestCase):
    """Every captured PR, in every gate state the gate can be in."""

    def test_every_real_pr_in_every_gate_state(self):
        tick_module()  # outside the loop: a missing tick must skip this test, not fail its count assertion
        prs = fixtures()
        n = 0
        for key, pr in sorted(prs.items()):
            cfg = ZSTACK_CFG if pr["repo"] == "injaneity/zstack" else CUA_CFG
            helds = (None, "hold list", "no-move")
            clefs = (None, "light", "standard", "deep", "human") if cfg.requires_clef else (None, "deep")
            reruns = (None, pr["headRefOid"])
            # local_pass: the body's own PASS head is the interesting case; a wrong one too
            locals_ = (frozenset(), frozenset({pr["headRefOid"]}), frozenset({"0" * 40}))
            for clef, held, rerun, loc in itertools.product(clefs, helds, reruns, locals_):
                with self.subTest(pr=key, clef=clef, held=held, rerun=bool(rerun), local=len(loc)):
                    old, new = both(pr, cfg=cfg, clef=clef, held=held, rerun_head=rerun, local_pass=loc)
                    n += 1
                    self.assertEqual(old, new)
        self.assertGreater(n, 500)

    def test_the_required_coverage_is_actually_in_the_fixtures(self):
        """Guard the guard: the fixtures really do contain the states the gate must handle."""
        prs = fixtures()
        seen = set()
        for key, pr in prs.items():
            cfg = ZSTACK_CFG if pr["repo"] == "injaneity/zstack" else CUA_CFG
            state = engine.check_state(pr.get("statusCheckRollup"), cfg)
            has_pass = engine.verify_pass(pr["headRefOid"], pr.get("body"), set())
            if pr["isDraft"]:
                seen.add("draft")
            if state[0] == "pending":
                seen.add("pending")
            if state[0] == "red" and has_pass:
                seen.add("red with a PASS")
            if pr["mergeable"] == "CONFLICTING":
                seen.add("conflicting")
            if not pr["isDraft"] and has_pass and state[0] == "green" and pr["mergeable"] == "MERGEABLE":
                seen.add("clean mergeable")
            if pr["repo"] == "injaneity/zstack":
                seen.add("zstack")
        for want in ("draft", "pending", "red with a PASS", "conflicting", "clean mergeable", "zstack"):
            self.assertIn(want, seen)


class NamedCases(unittest.TestCase):
    """One named test per behaviour, in the tick's own terms, so a failure names the rule that broke."""

    def pr(self, **kw):
        h = "a" * 40
        base = {"number": 5900, "headRefOid": h, "isDraft": False, "mergeable": "MERGEABLE",
                "labels": ["risk:light"], "body": f"change\nverify: PASS at {h}\nrisk: light\n",
                "statusCheckRollup": [chk("build"), chk("test")]}
        base.update(kw)
        return base

    def dec(self, pr, cfg=CUA_CFG, **kw):
        kw.setdefault("clef", "light")
        kw.setdefault("held", None)
        kw.setdefault("rerun_head", None)
        return both(pr, cfg=cfg, **kw)

    def test_draft(self):
        """A draft that passes goes ready, and the tick and the engine agree on the reason too."""
        old, new = self.dec(self.pr(isDraft=True))
        self.assertEqual(old, ("ready", "verify PASS, risk light"))
        self.assertEqual(new, old)

    def test_held(self):
        old, new = self.dec(self.pr(), held="hold list")
        self.assertEqual(old, ("skip", "hold: hold list"))
        self.assertEqual(new, old)

    def test_no_clef_score(self):
        old, new = self.dec(self.pr(), clef=None)
        self.assertEqual(old, ("wait", "awaiting clef score at head"))
        self.assertEqual(new, old)

    def test_clef_human(self):
        old, new = self.dec(self.pr(labels=["risk:standard"], body=f"verify: PASS at {'a'*40}\nrisk: standard"), clef="human")
        self.assertEqual(old, ("urgent", "clef human (declared standard)"))
        self.assertEqual(new, old)

    def test_clef_deep(self):
        old, new = self.dec(self.pr(labels=["risk:standard"], body=f"verify: PASS at {'a'*40}\nrisk: standard"), clef="deep")
        self.assertEqual(old, ("buffer", "clef deep: needs Zane (declared standard)"))
        self.assertEqual(new, old)

    def test_pending_checks(self):
        old, new = self.dec(self.pr(statusCheckRollup=[chk("build"), chk("e2e", status="IN_PROGRESS", conclusion=None)]))
        self.assertEqual(old, ("wait", "checks pending (1)"))
        self.assertEqual(new, old)

    def test_red_checks(self):
        red = self.pr(statusCheckRollup=[chk("build"), chk("e2e", conclusion="FAILURE")])
        first = self.dec(red)
        self.assertEqual(first[0], ("rerun", "red once: e2e"))
        self.assertEqual(first[1], first[0])
        again = self.dec(red, rerun_head="a" * 40)
        self.assertEqual(again[0], ("buffer", "red after rerun: e2e"))
        self.assertEqual(again[1], again[0])

    def test_conflicting(self):
        old, new = self.dec(self.pr(mergeable="CONFLICTING"))
        self.assertEqual(old, ("buffer", "merge conflict"))
        self.assertEqual(new, old)

    def test_clean_mergeable(self):
        old, new = self.dec(self.pr())
        self.assertEqual(old, ("merge", "green, verify PASS, risk light"))
        self.assertEqual(new, old)

    def test_zstack_draft_with_no_ci(self):
        """zstack's own #1: a draft with no checks at all. No Clef on this repo, so risk comes from the PR itself."""
        old, new = self.dec(self.pr(number=1, isDraft=True, statusCheckRollup=[],
                                    labels=[], body=f"verify: PASS at {'a'*40}\nrisk: low"),
                            cfg=ZSTACK_CFG, clef=None)
        self.assertEqual(old, ("ready", "verify PASS, risk light"))
        self.assertEqual(new, old)

    def test_zstack_no_pass_is_skipped(self):
        old, new = self.dec(self.pr(number=1, labels=[], body="no evidence line"), cfg=ZSTACK_CFG, clef=None)
        self.assertEqual(old, ("skip", "no verify PASS at head"))
        self.assertEqual(new, old)


class RiskParity(unittest.TestCase):
    """declared_risk, clef_tier, norm_tier, stricter: the same words in, the same tier out."""

    def test_declared_risk(self):
        tick = tick_module()
        cases = [(["risk:light"], ""), (["risk:high"], ""), (["risk:bogus"], ""), ([], "risk: deep"),
                 ([], "x\nrisk: standard"), ([], "RISK: low"), (["other:light"], ""), ([], None),
                 (["risk:light"], "risk: human"), (["risk:light:deep"], ""), (["risk:"], ""), (["risk"], "")]
        for labels, body in cases:
            with self.subTest(labels=labels, body=body):
                self.assertEqual(tick.declared_risk(labels, body),
                                 engine.declared_risk(labels, body, CUA_CFG))

    def test_clef_tier(self):
        tick = tick_module()
        title = "fix(cua-driver): a thing"
        cases = [("low", [], title, False), ("standard", ["src/a.rs"], title, False),
                 ("high", ["src/a.rs"], title, False), ("high", ["auth/token.rs"], title, False),
                 ("high", ["src/a.rs"], "add oauth", False), ("high", [".github/workflows/ci.yml"], title, False),
                 ("high", [".github/workflows/ci.yml"], title, True), ("bogus", [], title, False),
                 (None, [], title, False), ("HIGH", ["pairing.rs"], title, False)]
        for raw, files, t, outside in cases:
            with self.subTest(raw=raw, files=files, outside=outside):
                self.assertEqual(tick.clef_tier(raw, files, t, outside=outside),
                                 engine.clef_tier(raw, files, t, outside, CUA_CFG))

    def test_norm_and_stricter(self):
        tick = tick_module()
        for w in ("low", "LIGHT", "standard", " high ", "deep", "human", "bogus", "", None):
            self.assertEqual(tick.norm_tier(w), engine.norm_tier(w))
        for combo in itertools.product(("light", "standard", "deep", "human", None), repeat=3):
            self.assertEqual(tick.stricter(*combo), engine.stricter(*combo))


class CheckStateParity(unittest.TestCase):
    """check_state over the real rollups, and over the shapes the fixtures do not happen to contain."""

    def test_real_rollups(self):
        tick = tick_module()
        for key, pr in fixtures().items():
            with self.subTest(pr=key):
                self.assertEqual(tick.check_state(pr.get("statusCheckRollup")),
                                 engine.check_state(pr.get("statusCheckRollup"), CUA_CFG))

    def test_edge_shapes(self):
        tick = tick_module()
        cases = [
            [], None,
            [chk("cla", conclusion="FAILURE")],
            [chk("cla")],
            [chk("cla"), chk("e2e", conclusion="SKIPPED")],
            [chk("cla"), chk("validate")],
            [{"__typename": "StatusContext", "context": "codecov/patch", "state": "FAILURE"}],
            [{"__typename": "StatusContext", "context": "cla", "state": "FAILURE"}],
            [{"__typename": "StatusContext", "context": "x", "state": "PENDING"}],
            [{"__typename": "StatusContext", "context": "x", "state": "EXPECTED"}],
            [{"name": "x", "state": "SUCCESS"}],
            [chk("x", conclusion="NEUTRAL")],
            [chk("x", conclusion="CANCELLED")],
            [chk("x", started="2026-01-01T00:00:00Z", conclusion="FAILURE"),
             chk("x", started="2026-01-02T00:00:00Z", conclusion="SUCCESS")],
            [chk("x", started="2026-01-01T00:00:00Z", conclusion="SUCCESS"),
             chk("x", started="2026-01-02T00:00:00Z", conclusion="FAILURE")],
        ]
        for rollup in cases:
            with self.subTest(rollup=rollup):
                self.assertEqual(tick.check_state(rollup), engine.check_state(rollup, CUA_CFG))

    def test_no_ci_repo_treats_no_check_as_empty(self):
        """zstack has no CI: an empty rollup must stay 'empty' (wait), never 'green' (merge)."""
        self.assertEqual(engine.check_state([], ZSTACK_CFG), ("empty", [], set()))


class FuzzParity(unittest.TestCase):
    """Random rollups and labels, fixed seed: parity beyond the states we happened to capture."""

    def test_check_state_fuzz(self):
        import random
        rnd = random.Random(20261029)
        tick = tick_module()
        names = ["build", "test", "cla", "ci-gate", "e2e", "validate", "x"]
        for _ in range(400):
            rollup = []
            for _ in range(rnd.randrange(0, 7)):
                c = {"name": rnd.choice(names), "workflowName": rnd.choice(["CI", "CD", ""])}
                kind = rnd.randrange(3)
                if kind == 0:
                    c.update(__typename="StatusContext", state=rnd.choice(["PENDING", "EXPECTED", "SUCCESS", "FAILURE", "ERROR"]))
                elif kind == 1:
                    c.update(__typename="CheckRun", status=rnd.choice(["COMPLETED", "IN_PROGRESS", "QUEUED"]),
                             conclusion=rnd.choice(["SUCCESS", "FAILURE", "NEUTRAL", "SKIPPED", "CANCELLED", None]),
                             detailsUrl=rnd.choice([f"https://github.com/x/y/actions/runs/{rnd.randrange(1, 9)}/job/1", ""]))
                else:
                    c.update(state=rnd.choice(["SUCCESS", "FAILURE", "PENDING"]))
                c["startedAt"] = rnd.choice(["", "2026-10-25T01:00:00Z", "2026-10-26T01:00:00Z", "2026-10-27T01:00:00Z"])
                rollup.append(c)
            with self.subTest(rollup=rollup):
                self.assertEqual(tick.check_state(rollup), engine.check_state(rollup, CUA_CFG))

    def test_declared_risk_fuzz(self):
        import random
        rnd = random.Random(7)
        tick = tick_module()
        words = ["light", "low", "standard", "high", "deep", "human", "bogus", "", "Light"]
        for _ in range(200):
            labels = [rnd.choice(["risk:", "sev:", "other"]) + rnd.choice(words) for _ in range(rnd.randrange(0, 3))]
            body = "\n".join(rnd.choice([f"risk: {rnd.choice(words)}", f"risk:{rnd.choice(words)}",
                                         f"RISK: {rnd.choice(words)}", "nothing"]) for _ in range(rnd.randrange(0, 4)))
            with self.subTest(labels=labels, body=body):
                self.assertEqual(tick.declared_risk(labels, body),
                                 engine.declared_risk(labels, body, CUA_CFG))


class RetriggerParity(unittest.TestCase):
    def test_plan(self):
        tick = tick_module()
        when = "2026-10-27T00:00:00Z"
        prs = [
            {"number": 3, "isCrossRepository": True, "statusCheckRollup": [chk("ci-gate", started="2026-10-26T00:00:00Z")]},
            {"number": 1, "isCrossRepository": False, "statusCheckRollup": [chk("ci-gate", started="2026-10-26T00:00:00Z")]},
            {"number": 2, "isCrossRepository": False, "statusCheckRollup": [chk("ci-gate", started="2026-10-28T00:00:00Z")]},
            {"number": 4, "isCrossRepository": False, "statusCheckRollup": []},
            {"number": 9, "isCrossRepository": False, "statusCheckRollup": []},
        ]
        tracked = {1, 2, 3, 4}
        done = {"9@x"}
        for sha in ("x", "y"):
            with self.subTest(sha=sha):
                self.assertEqual(tick.retrigger_plan(prs, tracked, sha, when, done),
                                 engine.retrigger_plan(prs, tracked, sha, when, done))


class MiscParity(unittest.TestCase):
    def test_fingerprints(self):
        tick = tick_module()
        ps = list(fixtures().values())
        for p in ps:
            self.assertEqual(tick.pr_fp(p), engine.pr_fp(p))
        for a, b in itertools.product(ps[:8], ps[:8]):
            self.assertEqual(tick.fp_match(tick.pr_fp(a), tick.pr_fp(b)),
                             engine.fp_match(engine.pr_fp(a), engine.pr_fp(b)))
        seeded = {"head": "abcdef123", "seeded": True}
        self.assertTrue(engine.fp_match(seeded, {"head": "abcdef123456"}))
        self.assertFalse(engine.fp_match(seeded, {"head": "0123"}))

    def test_merge_audit_and_merge_log(self):
        tick = tick_module()
        merged = [{"number": 1, "mergedBy": {"login": "injaneity", "is_bot": False}, "mergedAt": "2026-10-27T01:00:00Z"},
                  {"number": 2, "mergedBy": {"login": "app/dependabot", "is_bot": True}, "mergedAt": "2026-10-27T02:00:00Z"},
                  {"number": 3, "mergedBy": {"login": "someone", "is_bot": False}, "mergedAt": "2026-10-27T03:00:00Z"},
                  {"number": 4, "mergedBy": {"login": "injaneity", "is_bot": False}, "mergedAt": "2026-10-27T04:00:00Z"},
                  {"number": 5, "mergedBy": None, "mergedAt": "2026-10-27T05:00:00Z"}]
        events = ["2026-10-27T01:00:00+08:00 merged cua#4 squash at abc: green", "noise"]
        for known in ({}, {"1": {}}, {"3": {}}):
            with self.subTest(known=known):
                self.assertEqual(tick.merge_audit(merged, tick.tick_merge_log(events), known),
                                 engine.merge_audit(merged, engine.tick_merge_log(events, "cua"), known, "injaneity"))

    def test_strip_negated(self):
        tick = tick_module()
        for t in ("never touch impl_.rs, fix the parser", "do not touch auth; change the label",
                  "leave the CI alone", "nothing special here", ""):
            self.assertEqual(tick.strip_negated(t), engine.strip_negated(t))

    def test_flip_lines(self):
        tick = tick_module()
        lines = ["- [started abc.1 27 Oct 01:00 SGT] fix pr #1: thing", "- [ ] untouched", "- [started xyz] other"]
        failed = {"abc.1": ("boom [x]", "29 Oct 11:00")}
        done = {"abc.1": "29 Oct 11:00", "xyz": "29 Oct 12:00"}
        self.assertEqual(tick.flip_failed(lines, failed), engine.flip_failed(lines, failed))
        self.assertEqual(tick.flip_done(lines, done), engine.flip_done(lines, done))

    def test_disk_guard_and_pg(self):
        tick = tick_module()
        runs = [{"state": "running", "alive": True}, {"state": "running", "alive": False}, {"state": "done", "alive": True}]
        for free in (5, 9, 10, 11, 40):
            self.assertEqual(tick.disk_guard_plan(free, runs), engine.disk_guard_plan(free, runs))
            self.assertEqual(tick.disk_guard_plan(free, runs, 20), engine.disk_guard_plan(free, runs, 20))
        self.assertEqual(tick.pg_alive(os.getpid()), engine.pg_alive(os.getpid()))

    def test_rules_block_replacement(self):
        tick = tick_module()
        text = f"a\n{tick.RULES_BEGIN}\nold\n{tick.RULES_END}\nz\n"
        block = [tick.RULES_BEGIN, "new", tick.RULES_END]
        self.assertEqual(tick.replace_rules_block(text, block),
                         engine.replace_rules_block(text, block, tick.RULES_BEGIN, tick.RULES_END))
        for bad in ("no markers", f"{tick.RULES_BEGIN}\nonly begin\n", f"{tick.RULES_END}\n{tick.RULES_BEGIN}\n"):
            self.assertIsNone(engine.replace_rules_block(bad, block, tick.RULES_BEGIN, tick.RULES_END))

    def test_webhook_body(self):
        tick = tick_module()
        for kind, ident in (("urgent", "cua#6124"), ("urgent", "holds"), ("x", None)):
            with self.subTest(ident=ident):
                self.assertEqual(tick.webhook_body(kind, ident, "why"),
                                 engine.webhook_body(kind, ident, "why", when=tick.now()))

    def test_reverify_line(self):
        tick = tick_module()
        h, o = "a" * 40, "b" * 40
        # the tick prefixes pi-onigiri lines with a run-from; the engine takes that prefix as a parameter
        self.assertEqual(tick.reverify_line("trycua/cua", 1, h, o, "why", None),
                         engine.reverify_line(1, h, o, "why", None))
        self.assertEqual(tick.reverify_line("injaneity/pi-onigiri", 1, h, o, "why", "high"),
                         engine.reverify_line(1, h, o, "why", "high", where=f"run from {tick.PIO} "))

    def test_verify_pass(self):
        tick = tick_module()
        h, o = "a" * 40, "b" * 40
        bodies = [None, "", f"verify: PASS at {h}", f"verify: PASS at {o}", f"x\nverify: PASS at {h}\ny",
                  f" verify: PASS at {h}", f"verify: PASS at {h[:9]}", "verify: PASS at"]
        for body in bodies:
            for local in (set(), {h}, {o}):
                with self.subTest(body=body, local=local):
                    self.assertEqual(tick.verify_pass(h, body, local), engine.verify_pass(h, body, local))

    def test_gate_started(self):
        tick = tick_module()
        cases = [None, [], [chk("ci-gate", started="2026-10-26T00:00:00Z")],
                 [chk("ci-gate", started="2026-10-26T00:00:00Z"), chk("ci-gate", started="2026-10-27T00:00:00Z")],
                 [chk("other", started="2026-10-26T00:00:00Z")],
                 [{"context": "ci-gate", "startedAt": "2026-10-26T00:00:00Z"}]]
        for rollup in cases:
            with self.subTest(rollup=rollup):
                self.assertEqual(tick.gate_started(rollup), engine.gate_started(rollup))

    def test_now_shape(self):
        tick = tick_module()
        import re as _re
        self.assertEqual(tick.now(), engine.now())
        self.assertRegex(engine.now(), _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$"))

    def test_dedupe_key(self):
        tick = tick_module()
        for args in (("merge-blocked", "cua#1", "a" * 40), ("x", "y", None)):
            self.assertEqual(tick.dedupe_key(*args), engine.dedupe_key(*args))

    def test_pending_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "pending.json")
            self.assertEqual(engine.load_pending(p), {})
            engine.save_pending({"7": {"sha": "s", "fork": False, "since": "t"}}, p)
            self.assertEqual(engine.load_pending(p), {"7": {"sha": "s", "fork": False, "since": "t"}})


class CarryPass(unittest.TestCase):
    """carry_pass takes a git callable, so both versions run against the same real repo and the same fakes."""

    def test_fake_git_same_verdict(self):
        tick = tick_module()
        h, p1, p2 = "a" * 40, "b" * 40, "c" * 40
        tree = "d" * 40

        def make(shas, ancestor_rc, mergetree_rc, tree_out):
            def git(*args):
                if args[0] == "rev-list":
                    return (0 if shas else 1), " ".join(shas)
                if args[0] == "merge-base":
                    return ancestor_rc, ""
                if args[0] == "merge-tree":
                    return mergetree_rc, f"{tree}\n"
                if args[0] == "rev-parse":
                    return 0, f"{tree_out}\n"
                raise AssertionError(args)
            return git

        cases = [
            make([h, p1, p2], 0, 0, tree), make([h, p1, p2], 1, 0, tree), make([h, p1, p2], 0, 1, tree),
            make([h, p1, p2], 0, 0, "e" * 40), make([h], 0, 0, tree), make([], 0, 0, tree),
            make([h, p1, p2, "x"], 0, 0, tree),
        ]
        for i, git in enumerate(cases):
            for passed in ({p1}, set(), {h}):
                with self.subTest(case=i, passed=passed):
                    self.assertEqual(tick.carry_pass(git, h, passed), engine.carry_pass(git, h, passed))


class CliApply(unittest.TestCase):
    """The --apply actions, driven with a fake `sh` so no live PR is touched."""

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        import gate
        self.gate = gate

    def run_gate(self, prs, apply=False, extra=()):
        calls = []
        tree = "c" * 40

        def fake_sh(args, **kw):
            calls.append(list(args))
            if args[0] == "git" and "rev-list" in args:
                return 0, f"{h} {old} {'d'*40}", ""
            if args[0] == "git" and "merge-tree" in args:
                return 0, f"{tree}\n", ""
            if args[0] == "git" and "rev-parse" in args:
                return 0, f"{tree}\n", ""
            return 0, "", ""

        real = (engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass)
        engine.sh = fake_sh
        engine.gh_json = lambda args, **kw: (calls.append(list(args)),
                                             {"mergeable": "MERGEABLE", "statusCheckRollup": prs[0]["statusCheckRollup"]})[1]
        self.gate.open_prs = lambda cfg, limit: prs
        self.gate.local_pass = lambda roots: set()
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                argv = ["--config", os.path.join(HERE, "..", "gate", "zstack.json"), "--state",
                        os.path.join(self._tmp(), "s.json")] + (["--apply"] if apply else []) + list(extra)
                self.gate.main(argv)
            return buf.getvalue(), calls
        finally:
            engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass = real

    def _tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def pr(self, **kw):
        h = "a" * 40
        base = {"number": 1, "headRefOid": h, "isDraft": False, "mergeable": "MERGEABLE", "labels": [],
                "body": f"verify: PASS at {h}\nrisk: light", "statusCheckRollup": [chk("build")], "title": "t"}
        base.update(kw)
        return base

    def test_no_apply_runs_no_gh_that_changes_anything(self):
        out, calls = self.run_gate([self.pr(isDraft=True)])
        self.assertIn("ready", out)
        self.assertFalse([c for c in calls if "merge" in c or "ready" in c])

    def test_apply_undrafts(self):
        out, calls = self.run_gate([self.pr(isDraft=True)], apply=True)
        self.assertTrue(any(c[:3] == ["gh", "pr", "ready"] for c in calls), calls)

    def test_apply_merges_with_the_configured_method_and_head_guard(self):
        out, calls = self.run_gate([self.pr()], apply=True)
        merge = [c for c in calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertTrue(merge, calls)
        self.assertIn("--squash", merge[0])
        self.assertIn("--match-head-commit", merge[0])
        self.assertIn("a" * 40, merge[0])

    def test_apply_reruns_once_then_stops(self):
        red = self.pr(statusCheckRollup=[chk("build", conclusion="FAILURE", run="999")])
        out, calls = self.run_gate([red], apply=True)
        reruns = [c for c in calls if c[:3] == ["gh", "run", "rerun"]]
        self.assertEqual(len(reruns), 1, calls)
        self.assertIn("999", reruns[0])
        self.assertFalse([c for c in calls if c[:3] == ["gh", "pr", "merge"]])

    def test_carried_pass_makes_an_old_head_legitimate(self):
        """A PASS carried across a clean main merge is not in the body. With --clone the gate checks and records it,
        so the PR is decided on its real merits instead of being skipped forever."""
        h, old = "a" * 40, "b" * 40
        pr = self.pr(headRefOid=h, body=f"verify: PASS at {old}", isDraft=True)
        calls = []
        tree = "c" * 40

        def fake_sh(args, **kw):
            calls.append(list(args))
            if args[0] == "git" and "rev-list" in args:
                return 0, f"{h} {old} {'d'*40}", ""
            if args[0] == "git" and "merge-tree" in args:
                return 0, f"{tree}\n", ""
            if args[0] == "git" and "rev-parse" in args:
                return 0, f"{tree}\n", ""
            return 0, "", ""

        real = (engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass)
        engine.sh = fake_sh
        engine.gh_json = lambda args, **kw: (calls.append(list(args)), {"mergeable": "MERGEABLE", "statusCheckRollup": []})[1]
        self.gate.open_prs = lambda cfg, limit: [pr]
        self.gate.local_pass = lambda roots: set()
        try:
            import io, contextlib, tempfile
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.gate.main(["--config", os.path.join(HERE, "..", "gate", "zstack.json"),
                                "--state", os.path.join(tempfile.mkdtemp(), "s.json"), "--clone", "/tmp/fake-clone"])
            out = buf.getvalue()
        finally:
            engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass = real
        self.assertIn("refs/pull/1/head", " ".join(" ".join(c) for c in calls if c[:2] == ["git", "-C"]))
        self.assertNotIn("no verify PASS", out, out)

    def test_held_pr_never_moves_even_with_apply(self):
        out, calls = self.run_gate([self.pr(isDraft=True)], apply=True, extra=["--held", "1"])
        self.assertIn("hold", out)
        self.assertFalse([c for c in calls if c[:3] in (["gh", "pr", "ready"], ["gh", "pr", "merge"])])


class ConfigIsTheOldConstants(unittest.TestCase):
    """gate/cua.json must be exactly the constants that were compiled into the tick, or the parity above is luck."""

    def test_cua_config_equals_the_tick_constants(self):
        tick = tick_module()
        self.assertEqual(CUA_CFG.repo, "trycua/cua")
        self.assertEqual(CUA_CFG.me, tick.ME)
        self.assertEqual(CUA_CFG.merge_method, "squash")
        self.assertEqual(CUA_CFG.label_prefix, "risk:")
        self.assertEqual(set(CUA_CFG.non_required_checks), set(tick.NON_REQUIRED_CHECKS))
        self.assertEqual(set(CUA_CFG.preapproved), {n for r, n in tick.PREAPPROVED if r == "trycua/cua"})
        self.assertTrue(CUA_CFG.requires_clef)
        self.assertEqual(CUA_CFG.clef_script, tick.CLEF)
        self.assertEqual(CUA_CFG.sensitive.pattern, tick.SENSITIVE.pattern)
        self.assertEqual(CUA_CFG.sensitive.flags, tick.SENSITIVE.flags)
        self.assertEqual(CUA_CFG.outside_sensitive.pattern, tick.OUTSIDE_SENSITIVE.pattern)

    def test_a_third_repo_config_reproduces_pi_onigiri(self):
        """The tick also gated injaneity/pi-onigiri, with no Clef. Same decisions from a config, no code change."""
        tick = tick_module()
        cfg = engine.Config({"repo": "injaneity/pi-onigiri", "me": tick.ME, "merge_method": "squash",
                             "label_prefix": "risk:"})
        self.assertFalse(cfg.requires_clef)
        h = "a" * 40
        prs = [{"number": 1, "headRefOid": h, "isDraft": False, "mergeable": "MERGEABLE", "labels": [],
                "body": f"verify: PASS at {h}\nrisk: standard", "statusCheckRollup": [chk("build")]},
               {"number": 2, "headRefOid": h, "isDraft": True, "mergeable": "MERGEABLE", "labels": ["risk:high"],
                "body": f"verify: PASS at {h}", "statusCheckRollup": []},
               {"number": 3, "headRefOid": h, "isDraft": False, "mergeable": "CONFLICTING", "labels": [],
                "body": f"verify: PASS at {h}", "statusCheckRollup": [chk("x", conclusion="FAILURE")]}]
        for p in prs:
            for clef in (None, "light", "human"):
                with self.subTest(pr=p["number"], clef=clef):
                    self.assertEqual(
                        tick.merge_decision(p, repo="injaneity/pi-onigiri", local_pass=set(), clef=clef,
                                            held=None, rerun_head=None),
                        engine.merge_decision(p, cfg=cfg, local_pass=set(), clef=clef, held=None, rerun_head=None))


class KnobsBite(unittest.TestCase):
    """Each knob changes a decision, so a silently ignored key fails a test instead of quietly mis-gating."""

    def pr(self, **kw):
        h = "a" * 40
        base = {"number": 10, "headRefOid": h, "isDraft": False, "mergeable": "MERGEABLE",
                "labels": ["risk:light"], "body": f"verify: PASS at {h}\nrisk: light", "statusCheckRollup": [chk("build")]}
        base.update(kw)
        return base

    def cfg(self, **over):
        d = {"repo": "x/y", "me": "u", "merge_method": "squash", "label_prefix": "risk:",
             "non_required_checks": [], "preapproved": [], "clef_script": "/dev/null",
             "sensitive": "secret", "outside_sensitive": "workflows"}
        d.update(over)
        return engine.Config(d)

    def test_non_required_checks(self):
        red = self.pr(statusCheckRollup=[chk("build"), chk("cla", conclusion="FAILURE")])
        self.assertEqual(engine.merge_decision(red, cfg=self.cfg(), local_pass=set(), clef="light", held=None,
                                               rerun_head=None)[0], "rerun")
        self.assertEqual(engine.merge_decision(red, cfg=self.cfg(non_required_checks=["cla"]), local_pass=set(),
                                               clef="light", held=None, rerun_head=None)[0], "merge")

    def test_preapproved(self):
        kw = dict(local_pass=set(), clef="human", held=None, rerun_head=None)
        self.assertEqual(engine.merge_decision(self.pr(), cfg=self.cfg(), **kw)[0], "urgent")
        self.assertEqual(engine.merge_decision(self.pr(), cfg=self.cfg(preapproved=[10]), **kw)[0], "merge")

    def test_label_prefix(self):
        p = self.pr(labels=["sev:deep"])
        self.assertEqual(engine.merge_decision(p, cfg=self.cfg(), local_pass=set(), clef="light", held=None,
                                               rerun_head=None)[1], "green, verify PASS, risk light")
        self.assertEqual(engine.merge_decision(p, cfg=self.cfg(label_prefix="sev:"), local_pass=set(), clef="light",
                                               held=None, rerun_head=None)[0], "buffer")

    def test_sensitive_and_outside_sensitive(self):
        self.assertEqual(engine.clef_tier("high", ["src/a.rs"], "t", False, self.cfg()), "deep")
        self.assertEqual(engine.clef_tier("high", ["src/secret.rs"], "t", False, self.cfg()), "human")
        self.assertEqual(engine.clef_tier("high", ["workflows/ci.yml"], "t", False, self.cfg()), "deep")
        self.assertEqual(engine.clef_tier("high", ["workflows/ci.yml"], "t", True, self.cfg()), "human")

    def test_no_clef_script_means_no_clef_wait(self):
        p = self.pr(labels=[], body=f"verify: PASS at {'a'*40}")
        self.assertEqual(engine.merge_decision(p, cfg=self.cfg(clef_script=None), local_pass=set(), clef=None,
                                               held=None, rerun_head=None)[0], "buffer")
        self.assertEqual(engine.merge_decision(p, cfg=self.cfg(), local_pass=set(), clef=None, held=None,
                                               rerun_head=None)[0], "wait")

    def test_empty_sensitive_names_nothing(self):
        cfg = engine.Config({"repo": "x/y", "me": "u", "merge_method": "squash", "label_prefix": "risk:"})
        self.assertEqual(engine.clef_tier("high", ["auth/token.rs"], "oauth", False, cfg), "deep")

    def test_merge_ref_has_verdicts(self):
        cfg = self.cfg()
        cases = [("0" * 40, {"status": "ahead"}, True),
                 ("0" * 40, {"status": "behind"}, False),
                 ("0" * 40, {"status": "identical"}, True),
                 ("0" * 40, None, None)]
        for ref, cmp_json, want in cases:
            real_sh, real_gh = engine.sh, engine.gh_json
            engine.sh = lambda args, **kw: (0, f"{ref}\trefs/pull/1/merge\n", "")
            engine.gh_json = lambda args, **kw: cmp_json
            try:
                with self.subTest(cmp=cmp_json):
                    self.assertEqual(engine.merge_ref_has(1, "a" * 40, cfg), want)
            finally:
                engine.sh, engine.gh_json = real_sh, real_gh
        real_sh = engine.sh
        engine.sh = lambda args, **kw: (0, "", "")
        try:
            self.assertIsNone(engine.merge_ref_has(1, "a" * 40, cfg))
        finally:
            engine.sh = real_sh

    def test_merge_ref_has_uses_the_config_repo(self):
        seen = []
        real_sh, real_gh = engine.sh, engine.gh_json
        engine.sh = lambda args, **kw: (seen.append(args), (0, f"{'a'*40}\trefs/pull/1/merge\n", ""))[1]
        engine.gh_json = lambda args, **kw: (seen.append(args), {"status": "ahead"})[1]
        try:
            self.assertTrue(engine.merge_ref_has(1, "a" * 40, self.cfg()))
            self.assertTrue(engine.merge_ref_has(1, "b" * 40, self.cfg()))
            self.assertTrue(any("github.com/x/y" in " ".join(a) for a in seen if isinstance(a, list)))
            self.assertTrue(any("repos/x/y/compare" in " ".join(a) for a in seen if isinstance(a, list)))
        finally:
            engine.sh, engine.gh_json = real_sh, real_gh


class Webhook(unittest.TestCase):
    def test_key_file_wins_and_url_is_never_baked_in(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            envf, keyf = os.path.join(d, "w.env"), os.path.join(d, "k")
            for path, body in ((envf, "WEBHOOK_URL=https://from-env-file\nWEBHOOK_KEY=envkey\n"), (keyf, "filekey\n")):
                with open(path, "w") as fh:
                    fh.write(body)
            self.assertEqual(engine.webhook_config(envf, keyf, None, {}), ("https://from-env-file", "filekey"))
            self.assertEqual(engine.webhook_config(envf, keyf, "https://explicit", {"GATE_WEBHOOK_URL": "https://e2"}),
                             ("https://explicit", "filekey"))
            self.assertEqual(engine.webhook_config(envf, keyf, None, {"GATE_WEBHOOK_URL": "https://e2"}),
                             ("https://e2", "filekey"))
            os.remove(keyf)
            self.assertEqual(engine.webhook_config(envf, keyf, None, {}), ("https://from-env-file", "envkey"))
            os.remove(envf)
            self.assertIsNone(engine.webhook_config(envf, keyf, None, {}))

    def test_no_webhook_url_in_the_tree(self):
        """This repo is public: no endpoint may be committed."""
        root = os.path.dirname(HERE)
        for dirpath, _, files in os.walk(root):
            if ".git" in dirpath:
                continue
            for f in files:
                path = os.path.join(dirpath, f)
                if path.endswith((".py", ".json", ".md", ".sh")) and os.path.abspath(path) != os.path.abspath(__file__):
                    with open(path, errors="ignore") as fh:
                        self.assertNotIn("automations/" + "webhook", fh.read(), path)


def chk(name, status="COMPLETED", conclusion="SUCCESS", started="2026-10-25T01:00:00Z", run="111", **kw):
    d = {"__typename": "CheckRun", "name": name, "workflowName": "CI", "status": status,
         "conclusion": conclusion, "startedAt": started,
         "detailsUrl": f"https://github.com/trycua/cua/actions/runs/{run}/job/9"}
    d.update(kw)
    return d


if __name__ == "__main__":
    unittest.main()

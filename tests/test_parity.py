"""The extracted engine must decide exactly what the tick decided.

Each case is a real PR state, captured with `gh` (see tests/fixtures/README.md), run through both the tick's
merge_decision and the engine's, over every (clef, held, rerun_head, local_pass) the gate can be in. Any
disagreement is a bug in the extraction, not a test to adjust.

The tick is read-only here: imported as a module from ONIGIRI_TICK (default the box's inspection copy).
"""
import contextlib
import gzip
import io
import itertools
import json
import os
import re
import shutil
import sys
import tempfile
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


def case_key(clef, held, rerun, loc):
    """A stable name for one gate state. Named, not counted: `local_pass` has three shapes, two of size one."""
    return f"{clef}|{held}|{int(bool(rerun))}|{loc}"


def locals_(head):
    """No local PASS, a PASS for this head, and a PASS for some other head."""
    return (frozenset(), frozenset({head}), frozenset({"0" * 40}))


def expected():
    """The old tick's own verdict for every fixture in every gate state, frozen by tests/freeze.py.

    Frozen so the sweep runs in CI, where no tick is checked out. Regenerate with `python3 tests/freeze.py`
    against a tick that has not been modified, and treat any diff as a real behaviour change to explain.
    """
    with gzip.open(os.path.join(HERE, "fixtures", "expected.json.gz")) as f:
        return json.load(f)["cases"]


def expected_provenance():
    """Which tick the frozen decisions came from. Recorded so the file can never be mistaken for a capture
    from a modified tick, or from this engine."""
    with gzip.open(os.path.join(HERE, "fixtures", "expected.json.gz")) as f:
        return json.load(f)["tick"]


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
        """Against the frozen tick verdicts, so this runs in CI where there is no tick.py."""
        prs, want = fixtures(), expected()
        self.assertEqual(sorted(prs), sorted(want), "fixtures and frozen decisions are out of step")
        n = 0
        for key, pr in sorted(prs.items()):
            cfg = ZSTACK_CFG if pr["repo"] == "injaneity/zstack" else CUA_CFG
            cases = want[key]["decisions"]
            helds = (None, "hold list", "no-move")
            clefs = (None, "light", "standard", "deep", "human") if cfg.requires_clef else (None, "deep")
            reruns = (None, pr["headRefOid"])
            locs = locals_(pr["headRefOid"])
            for clef, held, rerun, loc in itertools.product(clefs, helds, reruns, locs):
                k = case_key(clef, held, rerun, loc)
                with self.subTest(pr=key, case=k):
                    got = list(engine.merge_decision(pr, cfg=cfg, local_pass=set(loc), clef=clef, held=held,
                                                     rerun_head=rerun))
                    n += 1
                    self.assertEqual(cases[k], got, f"pr={key} case={k}")
                    self.assertEqual(len(cases), 18 * len(clefs))  # 3 helds x 2 rerun x 3 local_pass, per clef
        self.assertGreater(n, 500)

    def test_the_live_tick_still_agrees(self):
        """With a tick checked out, also compare against it live, so regeneration drift cannot hide."""
        tick_module()  # a missing tick skips here, and only here
        for key, pr in sorted(fixtures().items()):
            cfg = ZSTACK_CFG if pr["repo"] == "injaneity/zstack" else CUA_CFG
            clefs = ("light", "standard", "deep", "human") if cfg.requires_clef else (None, "deep")
            for clef in clefs:
                with self.subTest(pr=key, clef=clef):
                    old, new = both(pr, cfg=cfg, clef=clef, held=None, rerun_head=None)
                    self.assertEqual(old, new)

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
            # Decision paths, not field values: the guard used to check a field and call it coverage.
            rolls = pr.get("statusCheckRollup") or []
            fails = [c for c in rolls if c.get("conclusion") not in (engine.OK_CONCLUSIONS | {None, "PENDING"})]
            if state[0] == "red" and any(re.search(r"/actions/runs/\d+", c.get("detailsUrl") or "")
                                        for c in fails):
                seen.add("red with run ids")  # a *failing* check with a run id: the only shape `rerun` can act on
            if pr["mergeable"] == "UNKNOWN":
                # and it has to actually reach the UNKNOWN branch, not just wear the field
                action, why = engine.merge_decision(pr, cfg=cfg, local_pass={pr["headRefOid"]},
                                                    clef="light", held=None, rerun_head=None)
                if action == "wait" and "UNKNOWN" in why:
                    seen.add("mergeable unknown")
        for want in ("draft", "pending", "red with a PASS", "conflicting", "clean mergeable", "zstack",
                     "red with run ids", "mergeable unknown"):
            self.assertIn(want, seen, f"fixtures no longer cover {want!r}; recapture before trusting the sweep")

    def test_every_outcome_is_reachable_in_the_frozen_cases(self):
        """The sweep is only worth something if the frozen grid actually reaches all seven outcomes."""
        seen = {v[0] for case in expected().values() for v in case["decisions"].values()}
        self.assertEqual(seen, {"merge", "ready", "rerun", "buffer", "urgent", "wait", "skip"})

    def test_the_frozen_decisions_name_the_tick_they_came_from(self):
        """Provenance: without it a capture from a modified tick, or from this engine, would look identical."""
        prov = expected_provenance()
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", prov["sha256"]), prov)
        self.assertNotEqual(prov["tick_py"], "engine.py", prov)
        self.assertGreater(prov["lines"], 500, prov)


class HoldsParity(unittest.TestCase):
    """The holds file format is the tick's contract: a real /srv/onigiri/hold-prs.txt has to parse the same."""

    LINES = [
        "# a comment\nhold | 5900 | yes | patent\n",
        "no-move | #123 | no | staging\nhands-off-path | impl_.rs | yes | ours\n",
        "skip-author | f-trycua | yes | outside\n",
        "exception | docs/x.md | yes | backlog item 3 | pr | issue-42\n",
        "hands-off-user | someone | no | hands off\n",
        "hold | 1 | yes | a | b | c\n",  # a note containing pipes
        "",
        "\n\n# only comments\n\n",
    ]
    BAD = [
        "hold | 5900 | yes\n",  # too few fields
        "nonsense | 1 | yes | x\n",  # unknown kind
        "hold | 5900 | maybe | x\n",  # rules is not yes/no
        "hold |  | yes | x\n",  # no target
        "hold | abc | yes | x\n",  # not a number
        "exception | p | yes | only | two\n",  # too few exception fields
        "hold | 5900 | yes | x\nbroken\n",  # one bad row after a good one
    ]

    def test_good_files_parse_identically(self):
        tick = tick_module()
        for text in self.LINES:
            with self.subTest(text=text[:40]):
                self.assertEqual(tick.parse_holds(text), engine.parse_holds(text))

    def test_bad_files_are_rejected_by_both(self):
        tick = tick_module()
        for text in self.BAD:
            with self.subTest(text=text[:40]):
                with self.assertRaises(ValueError):
                    tick.parse_holds(text)
                with self.assertRaises(engine.HoldsUnreadable):
                    engine.parse_holds(text)

    def test_path_hit_agrees(self):
        tick = tick_module()
        holds = tick.parse_holds("hands-off-path | impl_.rs | yes | x\nhands-off-path | secret/ | yes | y\n")
        for path in ("src/impl_.rs", "impl_.rs", "src/notimpl_.rs", "myimpl_.rs", "docs/x.md"):
            with self.subTest(path=path):
                self.assertEqual(tick.path_hit(path, holds), engine.path_hit(path, holds))


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


class MiscParity(unittest.TestCase):
    def test_verify_pass(self):
        tick = tick_module()
        h, o = "a" * 40, "b" * 40
        bodies = [None, "", f"verify: PASS at {h}", f"verify: PASS at {o}", f"x\nverify: PASS at {h}\ny",
                  f" verify: PASS at {h}", f"verify: PASS at {h[:9]}", "verify: PASS at"]
        for body in bodies:
            for local in (set(), {h}, {o}):
                with self.subTest(body=body, local=local):
                    self.assertEqual(tick.verify_pass(h, body, local), engine.verify_pass(h, body, local))

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

    def run_gate(self, prs, apply=False, extra=(), state=None):
        """`state` is a path to share between calls: the rerun budget lives there, so a second call is the only
        way to observe "rerun once, then stop". --apply always gets a holds file, which the gate now demands."""
        calls = []
        tree = "c" * 40

        def fake_sh(args, **kw):
            calls.append(list(args))
            if args[:3] == ["gh", "api", "user"]:
                return 0, "injaneity\n", ""
            if args[0] == "git" and "rev-list" in args:
                return 0, f"{prs[0]['headRefOid']} {'b' * 40} {'d' * 40}", ""  # first parent, second, tree
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
                        state or os.path.join(self._tmp(), "s.json")] + (["--apply"] if apply else []) + list(extra)
                if apply and not any(x == "--holds" for x in argv):
                    argv += ["--holds", self._empty_holds()]
                self.gate.main(argv)
            return buf.getvalue(), calls
        finally:
            engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass = real

    def _empty_holds(self):
        """A holds file that holds nothing: --apply requires one, and "empty" is different from "absent"."""
        d = self._tmp()
        p = os.path.join(d, "holds.txt")
        with open(p, "w") as f:
            f.write("# nothing held\n")
        return p

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
        """Two runs over one state file: the first reruns the red head, the second must not rerun it again.

        A fresh state file per call would pass whichever way the budget behaved, so the second call is the test.
        """
        red = self.pr(statusCheckRollup=[chk("build", conclusion="FAILURE", run="999")])
        state = os.path.join(self._tmp(), "s.json")
        out1, calls1 = self.run_gate([red], apply=True, state=state)
        reruns = [c for c in calls1 if c[:3] == ["gh", "run", "rerun"]]
        self.assertEqual(len(reruns), 1, calls1)
        self.assertIn("999", reruns[0])
        self.assertFalse([c for c in calls1 if c[:3] == ["gh", "pr", "merge"]])

        out2, calls2 = self.run_gate([red], apply=True, state=state)
        self.assertFalse([c for c in calls2 if c[:3] == ["gh", "run", "rerun"]],
                         f"reran a second time: {calls2}")
        self.assertFalse([c for c in calls2 if c[:3] == ["gh", "pr", "merge"]])
        self.assertIn("buffer", out2.lower() + out1.lower())

    def test_carried_pass_makes_an_old_head_legitimate(self):
        """A PASS carried across a clean main merge is not in the body. With --clone the gate checks and records it,
        so the PR is decided on its real merits instead of being skipped forever."""
        h, old = "a" * 40, "b" * 40
        pr = self.pr(headRefOid=h, body=f"verify: PASS at {old}", isDraft=True)
        calls = []
        tree = "c" * 40

        def fake_sh(args, **kw):
            calls.append(list(args))
            if args[:3] == ["gh", "api", "user"]:
                return 0, "injaneity\n", ""
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


class HoldsAndPause(unittest.TestCase):
    """The safety surface the tick had and the first extraction dropped: a hold that cannot be read, a hold in
    the file, a hands-off path, a pause file. Each one has to stop an --apply, not just be parsed."""

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        import gate
        self.gate = gate

    def pr(self, **kw):
        h = "a" * 40
        base = {"number": 7, "headRefOid": h, "isDraft": False, "mergeable": "MERGEABLE", "labels": [],
                "author": {"login": "someone"}, "title": "t",
                "body": f"verify: PASS at {h}\nrisk: light", "statusCheckRollup": [chk("build")]}
        base.update(kw)
        return base

    def write(self, name, text):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = os.path.join(d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def run_gate(self, prs, extra=(), apply=True, files=()):
        """Run main() with a fake sh and a fake gh. `files` is what `pr view --json files` returns."""
        calls = []

        def fake_sh(args, **kw):
            calls.append(list(args))
            if args[:3] == ["gh", "api", "user"]:
                return 0, "injaneity\n", ""
            if args[0] == "git":
                return 0, "c" * 40 + "\n", ""
            return 0, "", ""

        def fake_gh(args, **kw):
            calls.append(list(args))
            if "files" in args:
                return {"files": [{"path": f} for f in files]}
            return {"mergeable": "MERGEABLE", "statusCheckRollup": prs[0]["statusCheckRollup"]}

        real = (engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass)
        engine.sh, engine.gh_json = fake_sh, fake_gh
        self.gate.open_prs = lambda cfg, limit: prs
        self.gate.local_pass = lambda roots: set()
        try:
            buf = io.StringIO()
            argv = ["--config", os.path.join(HERE, "..", "gate", "zstack.json"),
                    "--state", os.path.join(tempfile.mkdtemp(), "s.json")] + (["--apply"] if apply else [])
            argv += list(extra)
            if apply and "--holds" not in argv:
                argv += ["--holds", self.write("empty-holds.txt", "# nothing held\n")]
            with contextlib.redirect_stdout(buf):
                self.gate.main(argv)
            return buf.getvalue(), calls
        finally:
            engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass = real

    def acted(self, calls):
        return [c for c in calls if c[:3] in (["gh", "pr", "ready"], ["gh", "pr", "merge"])]

    def test_a_mergeable_pr_does_move_without_a_holds_file(self):
        """The control: without this the other tests prove nothing."""
        out, calls = self.run_gate([self.pr()])
        self.assertTrue(self.acted(calls), out)

    def test_a_pr_hold_in_the_file_stops_the_merge(self):
        holds = self.write("holds.txt", "hold | 7 | yes | patent\n")
        out, calls = self.run_gate([self.pr()], extra=["--holds", holds])
        self.assertFalse(self.acted(calls), out)
        self.assertIn("hold", out)

    def test_no_move_stops_it_too(self):
        holds = self.write("holds.txt", "no-move | #7 | yes | staging\n")
        out, calls = self.run_gate([self.pr()], extra=["--holds", holds])
        self.assertFalse(self.acted(calls), out)

    def test_apply_without_a_holds_file_refuses_to_run(self):
        """The default path must be closed too: no --holds and no config key is "I know nothing about holds",
        which is not the same as "nothing is held"."""
        with self.assertRaises(SystemExit) as e:
            self.run_gate([self.pr()], apply=True, extra=["--holds", ""])
        self.assertIn("needs a holds file", str(e.exception.code))

    def test_apply_with_a_holds_path_that_does_not_exist_refuses_to_run(self):
        """A typo'd or unmounted path is fatal, as it was in the tick: not "no holds"."""
        with self.assertRaises(SystemExit) as e:
            self.run_gate([self.pr()], apply=True, extra=["--holds", "/nonexistent/hold-prs.txt"])
        self.assertIn("refusing to act", str(e.exception.code))

    def test_a_hold_is_never_silently_dropped_because_the_file_is_bad(self):
        """Fail closed: an unparseable holds file must stop the run, not merge everything it failed to read."""
        holds = self.write("holds.txt", "hold | 7 | yes | patent\nthis row is nonsense\n")
        with self.assertRaises(SystemExit) as e:
            self.run_gate([self.pr()], extra=["--holds", holds])
        self.assertIn("refusing to act", str(e.exception.code))

    def test_a_hold_on_a_row_it_cannot_read_is_not_treated_as_no_hold(self):
        holds = self.write("holds.txt", "hold | not-a-number | yes | patent\n")
        with self.assertRaises(SystemExit):
            self.run_gate([self.pr()], extra=["--holds", holds])

    def test_a_hands_off_path_buffers_a_pr_that_touches_it(self):
        holds = self.write("holds.txt", "hands-off-path | impl_.rs | yes | ours\n")
        out, calls = self.run_gate([self.pr()], extra=["--holds", holds], files=["src/impl_.rs"])
        self.assertFalse(self.acted(calls), out)
        self.assertIn("hands-off path", out)

    def test_a_hands_off_path_does_not_buffer_an_unrelated_pr(self):
        holds = self.write("holds.txt", "hands-off-path | impl_.rs | yes | ours\n")
        out, calls = self.run_gate([self.pr()], extra=["--holds", holds], files=["src/other.rs"])
        self.assertTrue(self.acted(calls), out)

    def test_the_pause_file_stops_everything(self):
        pause = self.write("PAUSED", "paused for the patent hold\n")
        out, calls = self.run_gate([self.pr()], extra=["--pause-file", pause])
        self.assertFalse(self.acted(calls), out)
        self.assertIn("paused", out)

    def test_no_pause_file_means_act(self):
        with tempfile.TemporaryDirectory() as d:
            out, calls = self.run_gate([self.pr()], extra=["--pause-file", os.path.join(d, "PAUSED")])
        self.assertTrue(self.acted(calls), out)

    def test_set_risk_promotes_high_on_a_sensitive_path_to_human(self):
        """Recording `high` on an auth path is a human decision; storing `deep` would only buffer it."""
        cfg = engine.load(os.path.join(HERE, "..", "gate", "cua.json"))
        self.assertTrue(cfg.sensitive.search("src/auth.rs"), cfg.sensitive.pattern)
        self.assertEqual(engine.clef_tier("high", ["src/auth.rs"], "auth", outside=False, cfg=cfg), "human")
        # and a high that touches nothing sensitive stays deep, so the promotion is the path doing it
        self.assertEqual(engine.clef_tier("high", ["docs/x.md"], "docs", outside=False, cfg=cfg), "deep")

    def test_set_risk_only_ever_escalates(self):
        self.assertEqual(engine.stricter("human", "light"), "human")
        self.assertEqual(engine.stricter(None, "light"), "light")


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


class SetRiskAndScoreThroughTheCli(unittest.TestCase):
    """--set-risk and --score, driven through bin/gate.py. The previous pair of tests called engine helpers
    directly and never ran the code that reads the flag, which is where the bugs were."""

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        import gate
        self.gate = gate

    def call(self, argv, prs, files=(), view=None):
        """main(argv) with fake gh/sh. `view` overrides the per-PR gh view result."""
        calls = []
        state_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(state_dir, ignore_errors=True))
        state_path = os.path.join(state_dir, "s.json")

        def fake_gh(args, **kw):
            calls.append(list(args))
            if "files" in args:
                return {"files": [{"path": f} for f in files]}
            # a realistic view: head/title/body/files together, which is what the head pin compares
            return view if view is not None else {"headRefOid": prs[0]["headRefOid"], "title": prs[0]["title"],
                                                  "body": prs[0]["body"],
                                                  "files": [{"path": f} for f in files]}

        real = (engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass)
        engine.sh = lambda args, **kw: (calls.append(list(args)),
                                        (0, "injaneity\n" if args[:3] == ["gh", "api", "user"] else "", ""))[1]
        engine.gh_json = fake_gh
        self.gate.open_prs = lambda cfg, limit: list(prs)
        self.gate.local_pass = lambda roots: set()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.gate.main(["--config", os.path.join(HERE, "..", "gate", "cua.json"),
                                "--state", state_path] + list(argv))
        finally:
            engine.sh, engine.gh_json, self.gate.open_prs, self.gate.local_pass = real
        return buf.getvalue(), calls, state_path

    def pr(self, **kw):
        h = "a" * 40
        base = {"number": 7, "headRefOid": h, "isDraft": True, "mergeable": "UNKNOWN", "labels": [],
                "body": "", "title": "t", "author": {"login": "injaneity"}, "statusCheckRollup": []}
        base.update(kw)
        return base

    def test_set_risk_goes_through_clef_tier_and_records_under_the_canonical_key(self):
        out, _, state_path = self.call(["--set-risk", "07=high"], [self.pr()], files=["src/auth.rs"])
        with open(state_path) as f:
            st = json.load(f)
        self.assertEqual(list(st["clef"]), ["7"], "07 must be stored as 7, or the decision never reads it")
        self.assertEqual(st["clef"]["7"]["tier"], "human")  # high on an auth path is the owner's call
        self.assertEqual(st["clef"]["7"]["by"], "hand")

    def test_set_risk_only_ever_escalates(self):
        with tempfile.TemporaryDirectory() as d:
            state_path = os.path.join(d, "s.json")
            with open(state_path, "w") as f:
                json.dump({"clef": {"7": {"head": "a" * 40, "tier": "human", "by": "clef"}}}, f)
            out, _, _ = self.call(["--set-risk", "7=light", "--state", state_path], [self.pr()],
                                  files=["docs/x.md"])
            with open(state_path) as f:
                self.assertEqual(json.load(f)["clef"]["7"]["tier"], "human", out)

    def test_a_typo_in_set_risk_is_loud_not_silent(self):
        for bad in ("7=hgih", "seven=high", "99=high"):
            with self.subTest(spec=bad):
                with self.assertRaises(SystemExit):
                    self.call(["--set-risk", bad], [self.pr()])

    def test_a_hand_set_tier_does_not_satisfy_the_wait_for_a_score_rule(self):
        """Clef may be more cautious than the person typing, so a hand-set tier must not stop Clef running."""
        with tempfile.TemporaryDirectory() as d:
            state_path = os.path.join(d, "s.json")
            with open(state_path, "w") as f:
                json.dump({"clef": {"7": {"head": "a" * 40, "tier": "light", "by": "hand"}}}, f)
            out, calls, _ = self.call(["--score", "--state", state_path], [self.pr(isDraft=False)],
                                      files=["src/x.rs"])
            self.assertTrue([c for c in calls if any("clef-risk" in str(x) for x in c)],
                            f"Clef never ran: {calls}")

    def test_scoring_does_not_run_while_paused(self):
        with tempfile.TemporaryDirectory() as d:
            pause = os.path.join(d, "PAUSED")
            with open(pause, "w") as f:
                f.write("disk health\n")
            out, calls, _ = self.call(["--score", "--pause-file", pause], [self.pr(isDraft=False)],
                                      files=["src/x.rs"])
            self.assertFalse([c for c in calls if any("clef-risk" in str(x) for x in c)], calls)

    def test_a_score_for_a_head_that_moved_is_not_recorded(self):
        out, _, state_path = self.call(["--score"], [self.pr(isDraft=False)],
                                       view={"headRefOid": "b" * 40, "title": "t", "body": "", "files": []})
        with open(state_path) as f:
            st = json.load(f) if os.path.exists(state_path) else {}
        self.assertNotIn("7", st.get("clef") or {}, out)


class ConfigIsChecked(unittest.TestCase):
    def test_an_unknown_key_is_refused_not_ignored(self):
        """A typo'd key would silently leave the knob it meant to set at its default."""
        with open(os.path.join(HERE, "..", "gate", "zstack.json")) as f:
            base = json.load(f)
        engine.Config(dict(base))  # the real file is clean
        with self.assertRaises(ValueError) as e:
            engine.Config(dict(base, requiers_clef=True))
        self.assertIn("requiers_clef", str(e.exception))

    def test_both_shipped_configs_are_clean(self):
        for name in ("cua.json", "zstack.json"):
            with self.subTest(config=name):
                engine.load(os.path.join(HERE, "..", "gate", name))


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

class Webhook(unittest.TestCase):
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

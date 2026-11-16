import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("queue_under_test", str(PKG / "bin" / "queue"))
queue = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(queue)
BASE = queue.policy.load()
REPO, TIERS = BASE["merge"]["repo"], BASE["merge"]["tiers"]
H1, H2 = "a" * 40, "b" * 40


def cand(n, kind="issue", author="someone", labels=(), linked=(), created="2026-01-01"):
    ident = f"app-{n}" if kind == "issue" else f"app-{n}-review-{H1[:7]}"
    return dict(id=ident, kind=kind, n=n, head=None if kind == "issue" else H1, author={"login": author}, title=f"t{n}", body="b",
                url=f"u{n}", created=created, labels=list(labels), linked=list(linked))


class Forge:
    def __init__(self):
        self.prs, self.calls, self.claims, self.protected, self.statuses = {}, [], set(), True, []

    def gh(self, *a, stdin=None):
        self.calls.append(a)
        if a[:2] == ("pr", "view"):
            p = self.prs[int(a[2])]
            return 0, json.dumps({"number": int(a[2]), "state": p.get("state", "OPEN"), "headRefOid": p["head"],
                                  "labels": [{"name": x} for x in p.get("labels", [])]}), ""
        if a[:2] == ("pr", "merge"):
            self.prs[int(a[2])]["auto"] = "--auto" in a
            return 0, "", ""
        if a[0] == "api" and "statuses/" in a[3 if a[1] == "-X" else 1]:
            self.statuses.append((a[3].rsplit("/", 1)[1], [x for x in a if x.startswith("context=")][0]))
            return 0, "{}", ""
        if a[0] == "api" and a[1].endswith("required_status_checks"):
            checks = [BASE["merge"]["gate_check"], BASE["merge"]["verified_check"]] if self.protected else []
            return (0, json.dumps(checks), "") if self.protected else (1, "", "Branch not protected")
        if a[0] == "api":
            return 0, "main\n", ""
        raise AssertionError(a)

    def claim(self, repo, ident):
        if ident in self.claims:
            return "claimed-elsewhere"
        self.claims.add(ident)
        return None


class Case(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.holds = self.root / "hold-prs.txt"
        self.holds.write_text("# none\n")
        self.pol = json.loads(json.dumps(BASE))
        self.pol["host"].update(root=str(self.root), holds=str(self.holds))
        self.pol["run"]["threads"] = 2
        self.forge = Forge()
        for p in (patch.object(queue, "gh", self.forge.gh), patch.dict(os.environ, {"ONIGIRI_ROOT": str(self.root)})):
            p.start()
            self.addCleanup(p.stop)

    def items(self):
        return json.loads((self.root / "queue.json").read_text())["items"]

    def pick(self, cands, prs=(), dry=False):
        with patch.object(queue, "candidates", return_value=cands), patch.object(queue, "open_prs", return_value=set(prs)):
            return queue.pickup(self.pol, dry, claim=self.forge.claim)

    def add(self, ident, text="do the thing", priority=0):
        return queue.add(self.pol, ident, text, priority, claim=self.forge.claim)

    def to_working(self, ident, thread="t-1"):
        queue.step(self.pol, "start", ident)
        return queue.step(self.pol, "claim", ident, thread=thread)

    def to_verifying(self, ident, pr, head=H1, labels=()):
        self.forge.prs[pr] = {"head": head, "labels": list(labels)}
        return queue.step(self.pol, "ready", ident, pr=pr)


class Entry(Case):
    def test_pickup_queues_what_the_repo_has_and_says_why_it_skipped_the_rest(self):
        self.holds.write_text("hold | #7 | all | x\nskip-author | spammer | no | x\n")
        cands = [cand(1), cand(7, "review"), cand(2, labels=["hold"]), cand(3, linked=[7]), cand(4, author="spammer"),
                 cand(5, author="x[bot]"), cand(6, linked=[9])]
        out = self.pick(cands, prs=[9])
        self.assertEqual([x["id"] for x in out["queued"]], ["app-1"])
        self.assertEqual({x["id"]: x["why"] for x in out["skipped"]},
                         {"app-7-review-aaaaaaa": "held", "app-2": "held", "app-3": "held", "app-4": "held", "app-5": "held", "app-6": "pr"})
        self.assertEqual(self.pick(cands, prs=[9])["skipped"][0], {"id": "app-1", "why": "queued"})

    def test_a_dry_pickup_writes_and_claims_nothing(self):
        self.assertEqual(len(self.pick([cand(1)], dry=True)["queued"]), 1)
        self.assertFalse((self.root / "queue.json").exists())
        self.assertEqual(self.forge.claims, set())

    def test_an_unreadable_holds_file_or_a_failed_read_queues_nothing(self):
        self.holds.unlink()
        self.assertEqual(self.pick([cand(1)])["skipped"], [{"id": "app-1", "why": "holds-unreadable"}])
        self.holds.write_text("")
        with patch.object(queue, "gh", lambda *a, **k: (1, "", "gh down")):
            out = queue.pickup(self.pol, False, claim=self.forge.claim)
        self.assertEqual((out["queued"], bool(out["errors"])), ([], True))

    def test_a_machine_takes_its_labels_and_one_machine_takes_the_unlabelled(self):
        mac = {"labels": ["macOS"], "unlabelled": False, "all_labels": ["Linux", "macOS"]}
        linux = {"labels": ["Linux"], "unlabelled": True, "all_labels": ["Linux", "macOS"]}
        for labels, on_mac, on_linux in ((["macOS"], True, False), (["Linux"], False, True), (["bug"], False, True), ([], False, True),
                                         (["macOS", "Linux"], True, True)):
            self.assertEqual((queue.takes(labels, mac), queue.takes(labels, linux)), (on_mac, on_linux), labels)
        self.assertTrue(queue.takes(["Linux"], {"labels": [], "unlabelled": False, "all_labels": ["Linux"]}))
        self.pol["host"].update(mac)
        self.assertEqual(self.pick([cand(1, labels=["Linux"])])["skipped"], [{"id": "app-1", "why": "other-machine"}])

    def test_an_item_another_machine_claimed_is_left_to_it(self):
        self.forge.claims.add("app-1")
        self.assertEqual(self.pick([cand(1), cand(2)])["skipped"], [{"id": "app-1", "why": "claimed-elsewhere"}])
        self.assertEqual(list(self.items()), ["app-2"])

    def test_the_orchestrator_adds_a_task_with_a_priority(self):
        got = self.add("ship-docs", "Write the docs\nmore", priority=5)
        self.assertEqual((got["state"], got["source"], got["priority"], got["title"]), ("queued", "orchestrator", 5, "Write the docs"))
        for bad in (("ship-docs", "again"), ("bad id", "x"), ("empty", "  ")):
            with self.subTest(bad=bad), self.assertRaises(queue.Refused):
                self.add(*bad)
        self.forge.claims.add("taken")
        with self.assertRaisesRegex(queue.Refused, "claimed-elsewhere"):
            self.add("taken", "x")

    def test_an_urgent_label_goes_ahead_of_the_rest(self):
        self.pick([cand(1), cand(2, labels=["urgent"])])
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "app-2")

    def test_the_tier_is_the_strictest_named_and_the_strictest_of_all_when_none_is(self):
        self.assertEqual(queue.strictest(TIERS, "light", "deep", "standard"), "deep")
        self.assertEqual(queue.strictest(TIERS, None, "nope"), TIERS[-1])
        self.assertEqual(queue.labelled(["risk:deep", "risk:nope", "bug"], TIERS), ["deep"])


class Launch(Case):
    def test_the_highest_priority_goes_first_then_the_oldest(self):
        self.add("low", priority=1)
        self.add("high", priority=9)
        self.add("high-later", priority=9)
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "high")
        with self.assertRaisesRegex(queue.Refused, "low is not next: high is"):
            queue.step(self.pol, "start", "low")
        self.to_working("high")
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "high-later")

    def test_one_launch_at_a_time_and_never_past_the_thread_cap(self):
        for x in ("a", "b", "c"):
            self.add(x)
        queue.step(self.pol, "start", "a")
        self.assertEqual(queue.next_up(self.pol)["waiting"], "a launch is under way")
        with self.assertRaisesRegex(queue.Refused, "a launch is under way"):
            queue.step(self.pol, "start", "b")
        queue.step(self.pol, "claim", "a", thread="t-a")
        self.to_working("b", "t-b")
        got = queue.next_up(self.pol)
        self.assertEqual((got["launch"], got["waiting"]), (None, "the machine is at its thread cap"))

    def test_a_started_item_whose_thread_did_not_start_goes_back_and_a_stale_one_is_listed(self):
        self.add("a")
        queue.step(self.pol, "start", "a")
        self.assertEqual(queue.next_up(self.pol)["recover"], [])
        with patch.object(queue, "STALE_S", 0):
            self.assertEqual(queue.next_up(self.pol)["recover"], ["a"])
        self.assertEqual(queue.step(self.pol, "requeue", "a")["state"], "queued")
        with self.assertRaisesRegex(queue.Refused, "a is queued, not starting"):
            queue.step(self.pol, "claim", "a", thread="t")

    def test_a_held_item_is_passed_over_at_launch_too(self):
        self.pick([cand(1, "review"), cand(2)])
        self.holds.write_text("hold | #1 | all | x\n")
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "app-2")

    def test_the_brief_tells_the_thread_how_to_report(self):
        self.add("a", "Fix the parser")
        text = queue.next_up(self.pol)["launch"]["brief"]
        for want in ("# a", "Fix the parser", f"{PKG}/bin/queue ready a <pr number>", f"{PKG}/bin/queue raise a", "never merge"):
            self.assertIn(want, text)


class Merge(Case):
    def setUp(self):
        super().setUp()
        self.pol["run"]["threads"] = 9

    def passed(self, risk="light", labels=(), ident="a", pr=40):
        self.add(ident)
        self.to_working(ident)
        self.to_verifying(ident, pr, labels=labels)
        return queue.verified(self.pol, ident, "pass", risk)

    def test_a_pass_on_a_low_risk_item_posts_the_status_on_that_head_and_arms_auto_merge(self):
        got = self.passed()
        self.assertEqual((got["state"], got["armed"], got["say"]), ("passed", True, "it merges itself once CI is green"))
        self.assertEqual(self.forge.statuses, [(H1, f"context={BASE['merge']['verified_check']}")])
        self.assertTrue(self.forge.prs[40]["auto"])

    def test_high_risk_a_risk_label_on_the_pr_a_hold_or_no_branch_protection_leaves_it_to_the_owner(self):
        self.assertEqual(self.passed(risk="deep")["say"], "the owner merges it: its risk is deep")
        self.assertEqual(self.passed(labels=["risk:human"], ident="b", pr=41)["say"], "the owner merges it: its risk is human")
        self.holds.write_text("hold | #42 | all | x\n")
        self.assertEqual(self.passed(ident="c", pr=42)["say"], "the owner merges it: it has a hold row")
        self.forge.protected = False
        self.assertIn("branch protection does not require", self.passed(ident="d", pr=43)["say"])
        self.assertEqual([p.get("auto") for p in self.forge.prs.values()], [None, None, None, None])
        self.assertEqual(len(self.forge.statuses), 4)

    def test_a_head_that_moved_while_it_was_verified_gets_no_status(self):
        self.add("a")
        self.to_working("a")
        self.to_verifying("a", 40)
        self.forge.prs[40]["head"] = H2
        got = queue.verified(self.pol, "a", "pass", "light")
        self.assertEqual((got["state"], self.forge.statuses), ("working", []))

    def test_a_failed_verification_goes_back_to_the_thread_until_the_attempts_are_spent(self):
        self.add("a")
        self.to_working("a")
        cap = self.pol["ci"]["fix_attempts"]
        for n in range(1, cap + 1):
            self.to_verifying("a", 40)
            got = queue.verified(self.pol, "a", "fail")
            self.assertEqual((got["fixes"], got["state"]), (n, "working" if n < cap else "owner"))
        with self.assertRaises(queue.Refused):
            queue.step(self.pol, "ready", "a", pr=40)

    def test_raising_the_risk_turns_auto_merge_off_and_a_later_pass_does_not_arm_it(self):
        self.passed()
        got = queue.raise_risk(self.pol, "a")
        self.assertEqual((got["armed"], got["risk"], self.forge.prs[40]["auto"]), (False, TIERS[-1], False))
        self.add("b")
        self.to_working("b", "t-b")
        queue.raise_risk(self.pol, "b")
        self.to_verifying("b", 41)
        self.assertEqual(queue.verified(self.pol, "b", "pass", "light")["armed"], False)

    def test_sweep_closes_merged_items_sends_a_new_head_back_to_work_and_disarms_a_held_one(self):
        self.passed()
        self.passed(ident="b", pr=41)
        self.passed(ident="c", pr=42)
        self.forge.prs[40]["state"] = "MERGED"
        self.forge.prs[41]["head"] = H2
        self.holds.write_text("hold | #42 | all | x\n")
        moved = {x["id"]: (x["now"], x["why"]) for x in queue.sweep(self.pol)}
        self.assertEqual(moved, {"a": ("done", "merged"), "b": ("working", "a new head after it passed"),
                                 "c": ("passed", "held, so auto-merge is off")})
        self.assertEqual(self.forge.prs[42]["auto"], False)
        self.assertEqual(queue.sweep(self.pol), [])

    def test_the_steps_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        self.assertEqual(queue.STEPS, {"start": ("queued", "starting"), "claim": ("starting", "working"),
                                       "requeue": ("starting", "queued"), "ready": ("working", "verifying")})
        for name, to in (("Start(m, i)", "starting"), ("Claim(m)", "working"), ("Requeue(m)", "queued"), ("Ready(m, i)", "verifying"),
                         ("Pass(m, i)", "passed")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states - {"none"}, {*queue.ACTIVE, "queued", "owner", "done"})


class Cli(Case):
    def test_the_commands(self):
        out = io.StringIO()
        with patch.object(queue.policy, "load", return_value=self.pol), patch.object(queue, "forge_claim", self.forge.claim), \
                patch("sys.stdout", out):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["start", "a"]), 0)
            self.assertEqual(queue.main(["claim", "a", "t-1"]), 0)
            self.forge.prs[40] = {"head": H1}
            self.assertEqual(queue.main(["ready", "a", "40"]), 0)
            self.assertEqual(queue.main(["verified", "a", "pass"]), 2)
            self.assertEqual(queue.main(["verified", "a", "pass", "extreme"]), 2)
            self.assertEqual(queue.main(["verified", "a", "pass", "light"]), 0)
            self.assertEqual(queue.main(["sweep"]), 0)
            self.assertEqual(queue.main(["list"]), 0)
            self.assertEqual(queue.main(["next"]), 0)
            self.assertEqual(queue.main(["nope"]), 2)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["start", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: no item zzz\n")
        self.assertEqual(self.items()["a"]["state"], "passed")


if __name__ == "__main__":
    unittest.main()

import importlib.machinery
import importlib.util
import json
import re
import subprocess
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("policy_under_test", str(PKG / "bin" / "policy"))
policy = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(policy)
POLICY, RULES, STATE = policy.load(), policy.rules(), policy.state()

VERBATIM = ("skills/statechart/", "skills/create-formal-verification-skill/", "skills/maintain-formal-verification-skill/",
            "skills/cua/")


def tracked(*globs):
    out = subprocess.run(["git", "-C", str(PKG), "ls-files", "-z", "--", *globs], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def prose():
    return [f for f in tracked("skills/*.md", "skills/**/*.md", "agents/*.md", "prompts/*.md", "AGENTS.md")
            if not f.startswith(VERBATIM)]


def restated(text, pol):
    hits = []
    words = {"patent.held_words": pol["patent"]["held_words"], "people.me": [pol["people"]["me"]],
             "merge.gate_check": [pol["merge"]["gate_check"]], "patent.private_repo": [pol["patent"]["private_repo"]]}
    for v in pol["models"].values():
        for s in v if isinstance(v, list) else [v]:
            words.setdefault("models", []).append(s["model"])
    for key, vals in words.items():
        for v in vals:
            for m in re.finditer(rf"(?<![\w/.-]){re.escape(v)}(?![\w/-])", text, re.I):
                hits.append((key, m.group(0)))
    h, s, p = pol["hourly"], pol["slots"], pol["pickup"]
    numbers = {
        "hourly.floor_gb": rf"\b{h['floor_gb']} ?G(B|iB)?\b",
        "run.idle_min": rf"\b({pol['run']['idle_min']} ?min(ute)?s?|{pol['run']['idle_min'] / 60:g} ?h(ours?)?)\b",
        "hourly.merge_stale_min": rf"\b{h['merge_stale_min']} ?min(ute)?s?\b",
        "hourly.batch_hour": rf"\b0?{h['batch_hour']}:00 SGT\b",
        "hourly.file_cap": rf"\bat most {h['file_cap']}\b",
        "hourly.lume_prune_age_h": rf"\b{h['lume_prune_age_h']} ?h(ours?)?\b",
        "slots.auto": rf"\b{s['auto']} (auto )?slots?\b",
        "slots.hand": rf"\b{s['hand']} hand\b",
        "ci.fix_attempts": rf"\b{pol['ci']['fix_attempts']} (fix )?attempts\b",
        "pickup.interval_s": rf"\b({p['interval_s']} ?s|{p['interval_s'] // 60} ?min(ute)?s?)\b",
        "pickup.deadline_s": rf"\b({p['deadline_s']} ?s|{p['deadline_s'] // 60} ?min(ute)?s?)\b",
    }
    for key, rx in numbers.items():
        hits += [(key, m.group(0)) for m in re.finditer(rx, text)]
    return hits


class Rules(unittest.TestCase):
    def test_the_rules_have_one_home_and_bin_pi_run_appends_their_rendering(self):
        self.assertFalse((PKG / "skills" / "onigiri-mode" / "hard-rules.md").exists())
        md = policy.render(RULES)
        self.assertTrue(md.startswith("## Hard rules\n\n"))
        self.assertEqual([x for x in md.splitlines() if x.startswith("## ")], ["## Hard rules"])
        self.assertEqual([x for x in md.splitlines() if x.startswith("### ")],
                         ["### Work", "### Patent hold", "### Commits and PR text", "### Checks", "### Scope", "### Merging",
                          "### Changing these rules"])
        self.assertIn("`config/rules.toml`", (PKG / "skills" / "onigiri-mode" / "SKILL.md").read_text())

    def test_no_prose_carries_a_second_copy_of_a_rule(self):
        texts = [r["text"] for s in RULES["section"] for r in s["rule"]]
        copies = [(f, t[:60]) for f in prose() for t in texts if len(t) > 50 and t in (PKG / f).read_text()]
        self.assertEqual(copies, [])

    def test_an_agent_quotes_rules_that_exist(self):
        ids = {r["id"] for s in RULES["section"] for r in s["rule"]}
        for f in tracked("agents/*.md"):
            for rid in re.findall(r"^\{\{rule ([a-z-]+)\}\}$", (PKG / f).read_text(), re.M):
                self.assertIn(rid, ids, f)

    def test_the_12_nov_retro_lines_are_rules_word_for_word(self):
        md = policy.render(RULES)
        for line in ("When Zane names a priority, a hand-pool brief serving it takes the next free slot in either pool.",
                     "Delete stopped `cua-e2e-*` lume VMs older than 24h whose run is collected; never a running VM or a seed."):
            self.assertIn(line[:-1], md)

    def test_render_of_named_rules_is_one_bullet_each_in_order(self):
        self.assertEqual(policy.render(RULES, ["scope-no-numbers", "patent-scope"]).splitlines(),
                         ["- Bench numbers and timings stay out of commits and PR text.",
                          "- The patent hold decides only which repo a change is pushed to. Work on pairing, Teleport and Keyvault normally."])
        with self.assertRaisesRegex(policy.PolicyError, "no rule nope"):
            policy.render(RULES, ["nope"])

    def test_a_patent_or_risk_rule_that_drifts_from_policy_is_refused(self):
        def bad(rid, old, new):
            data = json.loads(json.dumps(RULES))
            r = next(r for s in data["section"] for r in s["rule"] if r["id"] == rid)
            self.assertIn(old, r["text"])
            r["text"] = r["text"].replace(old, new)
            return data
        for rid, old, new, why in (("issue-close", "`trycua/cua`", "`trycua/other`", "`trycua/cua`"),
                                   ("salvage-fixes", "`Fixes #<issue>`", "`Closes #<issue>`", "`Fixes #<issue>`"),
                                   ("patent-repo", "`trycua/cua-staging`", "`trycua/other`", "trycua/cua-staging"),
                                   ("patent-scope", "Keyvault", "Vault", "keyvault"),
                                   ("merge-risk", "light|standard|deep|human", "light|deep|human", "light|standard|deep|human"),
                                   ("lume-prune", "older than 24h", "older than 48h", "older than 24h")):
            with self.subTest(rid=rid), self.assertRaisesRegex(policy.PolicyError, re.escape(why)):
                policy.validate_rules(bad(rid, old, new), POLICY)
        dup = json.loads(json.dumps(RULES))
        dup["section"][0]["rule"].append(dict(dup["section"][0]["rule"][0]))
        with self.assertRaisesRegex(policy.PolicyError, "two rules have the id work-path"):
            policy.validate_rules(dup, POLICY)


class NoRestatedValues(unittest.TestCase):
    def test_skills_name_config_keys_instead_of_values(self):
        found = [f"{f}: {t!r} restates {k}" for f in prose() for k, t in restated((PKG / f).read_text(), POLICY)]
        self.assertEqual(found, [], "name the config/policy.toml key instead of its value")

    def test_the_check_catches_what_the_playbook_said_before(self):
        old = {"it has at least 20G free": "hourly.floor_gb", "no progress for 2.5h": "run.idle_min",
               "(none in the last 55 minutes": "hourly.merge_stale_min", "The first pass after 08:00 SGT": "hourly.batch_hour",
               "at most 10 a pass": "hourly.file_cap", "lume VMs older than 24h": "hourly.lume_prune_age_h",
               "we should have 3 slots for autopickup and up to 5 hand started work": "slots.auto",
               "issues assigned to injaneity": "people.me", "green ci-gate at a stable head": "merge.gate_check",
               "Work on pairing, Teleport and Keyvault normally": "patent.held_words"}
        for line, key in old.items():
            with self.subTest(line=line):
                self.assertIn(key, [k for k, _ in restated(line, POLICY)])

    def test_dates_times_and_step_numbers_are_not_values(self):
        for line in ("Zane, 12 Nov 12:47 SGT", "steps 0 to 5", "8. **Merge", "the 11 Nov retro", "`cua-<n>-review-<head7>`",
                     "(Zane, 10 Nov 19:13)", "repairing"):
            self.assertEqual(restated(line, POLICY), [], line)


class StateFiles(unittest.TestCase):
    JOIN = re.compile(r"os\.path\.join\((?!PKG\b)[^()]*?(?:\([^()]*\))?[^()]*?"
                      r"\"([\w.-]+\.(?:json|log|txt|md|lock)|[A-Z]+|pushed|last-run|auto-pause)\"\)")
    FSTR = re.compile(r"f\"\{(?:ROOT|DIR|root|d)\}/([\w.-]+)\"")
    DIRS = {"tick", "review", "pickup", "sync"}
    CODE = ("bin/pi-run", "bin/hourly", "bin/pickup", "bin/hold-row", "bin/check-host", "hosts/vm/merge_job.py",
            "hosts/vm/patent_push.py", "hosts/mini/sync-from-vm.sh", "hosts/vm/cron-wrap.sh")

    def test_every_state_file_the_code_opens_is_declared(self):
        declared = {Path(f["path"]).name for f in STATE["file"].values()}
        missing = []
        for f in self.CODE:
            text = (PKG / f).read_text()
            for name in {*self.JOIN.findall(text), *self.FSTR.findall(text)} - self.DIRS:
                if name not in declared:
                    missing.append(f"{f} opens {name}, which config/state.toml does not declare")
        self.assertEqual(missing, [])

    def test_each_declared_writer_and_reader_names_its_file(self):
        wrong = []
        for key, f in STATE["file"].items():
            name = Path(f["path"]).name
            for who in [f["writer"], *f["readers"]]:
                p = PKG / who.split()[0]
                if who.split()[0].startswith(("bin/", "hosts/", "extensions/")):
                    if not p.is_file():
                        wrong.append(f"{key}: {who} does not exist")
                    elif name not in p.read_text():
                        wrong.append(f"{key}: {who} never names {name}")
        self.assertEqual(wrong, [])

    def test_a_bad_entry_is_refused(self):
        good = {"path": "x.json", "host": "each", "format": "json", "writer": "bin/x", "readers": ["bin/y"], "schema": "{a}"}
        for change, why in (({"path": "/abs"}, "under \\$ONIGIRI_ROOT"), ({"format": "yaml"}, "format must be one of"),
                            ({"readers": "bin/y"}, "needs a host"), ({"extra": 1}, "unknown key file.x.extra")):
            with self.subTest(change=change), self.assertRaisesRegex(policy.PolicyError, why):
                policy.validate_state({"schema": 1, "file": {"x": {**good, **change}}})


class NoVerifiedComment(unittest.TestCase):
    def test_nothing_asks_for_the_verified_pr_comment(self):
        rx = re.compile(r"verified <|verified comment|verified-comment|`verified <sha>`|post (the|its) verdict on the PR", re.I)
        rendered = policy.render(RULES)
        hits = [f"config/rules.toml: {m.group(0)}" for m in rx.finditer(rendered)]
        for f in [*prose(), "bin/new-brief", "bin/pi-run", "bin/pickup", "bin/hourly", "README.md", "docs/SUBAGENT.md"]:
            hits += [f"{f}: {m.group(0)}" for m in rx.finditer((PKG / f).read_text())]
        self.assertEqual(hits, [])
        self.assertIn("run report", next(r["text"] for s in RULES["section"] for r in s["rule"] if r["id"] == "checks-verifier"))


if __name__ == "__main__":
    unittest.main()

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

PKG = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("policy_under_test", str(PKG / "queue.py"))
policy = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(policy)
POLICY, INV = policy.load(), policy.invariants()

VERBATIM = ("skills/statechart/", "skills/create-formal-verification-skill/", "skills/maintain-formal-verification-skill/")


def tracked(*globs):
    out = subprocess.run(["git", "-C", str(PKG), "ls-files", "-z", "--", *globs], capture_output=True, text=True, check=True).stdout
    return [f for f in out.split("\0") if f]


def prose():
    return [f for f in tracked("skills/*.md", "skills/**/*.md", "agents/*.md", "pi/prompts/*.md", "AGENTS.md")
            if not f.startswith(VERBATIM)]


def code():
    return [f for f in tracked("queue.py", "pi/*.ts", "pi/**/*.ts", "hooks/*") if not f.endswith(".md")]


def restated(text, pol, repo=False):
    hits = []
    words = {"patent.held_words": pol["patent"]["held_words"], "people.me": [pol["people"]["me"]], "people.owner": [pol["people"]["owner"]],
             "merge.gate_check": [pol["merge"]["gate_check"]],
             "patent.private_repo": [pol["patent"]["private_repo"]]}
    if repo:
        words["merge.repo"] = [pol["merge"]["repo"]]
    for v in pol["models"].values():
        for s in v if isinstance(v, list) else [v]:
            words.setdefault("models", []).append(s["model"])
    for key, vals in words.items():
        for v in vals:
            for m in re.finditer(rf"(?<![\w/.-]){re.escape(v)}(?![\w/-])", text, re.I):
                hits.append((key, m.group(0)))
    numbers = {
        "run.idle_min": rf"\b({pol['run']['idle_min']} ?min(ute)?s?|{pol['run']['idle_min'] / 60:g} ?h(ours?)?)\b",
    }
    for key, rx in numbers.items():
        hits += [(key, m.group(0)) for m in re.finditer(rx, text)]
    return hits


class Invariants(unittest.TestCase):
    def test_they_have_one_home_per_level_and_render_under_one_heading(self):
        md = policy.render(INV)
        self.assertTrue(md.startswith("## Invariants\n\n"))
        self.assertEqual([x for x in md.splitlines() if x.startswith("#")],
                         ["## Invariants", "### Always", "### In this repo"])
        self.assertNotIn(INV["guidance"][0]["text"], md)
        self.assertEqual([r["id"] for r in INV["stack"]],
                         ["merge-verified", "held", "turn-ends-verified", "one-thread", "one-launch", "own-controls", "publish"])
        self.assertEqual([r["id"] for r in INV["repo"]], ["patent", "warm", "text", "issue-close", "teammates"])
        self.assertIn("`config/invariants.toml`", (PKG / "skills" / "onigiri-mode" / "SKILL.md").read_text())

    def test_every_invariant_names_where_it_is_enforced_and_a_test_that_exists(self):
        for r in INV["stack"] + INV["repo"]:
            path, _, name = r["test"].partition("::")
            self.assertTrue((PKG / path).is_file(), r["id"])
            self.assertIn(name, (PKG / path).read_text(), r["id"])
            self.assertTrue(r["enforced"].strip(), r["id"])
            if path.endswith(".tla"):
                self.assertRegex((PKG / "model" / "Queue.cfg").read_text(), rf"(?m)^(INVARIANT|PROPERTY) {name}$", r["id"])
                self.assertIn(f'"{name}"', (PKG / "model" / "queue_mutants.py").read_text(), f"{r['id']}: no mutant breaks {name}")
        proved = [r["id"] for r in INV["stack"] if r["test"].startswith("model/")]
        self.assertEqual(proved, ["merge-verified", "held", "turn-ends-verified", "one-thread", "one-launch"])
        for miss in ("enforced", "test"):
            row = {"id": "a", "text": "t", "enforced": "e", "test": "x"}
            del row[miss]
            with self.assertRaisesRegex(policy.Refused, f"missing the stack's invariant.{miss}"):
                policy.entries("the stack's", {"invariant": [row]}, POLICY, set())

    def test_no_prose_carries_a_second_copy_of_one(self):
        texts = [r["text"] for part in INV.values() if isinstance(part, list) for r in part]
        copies = [(f, t[:60]) for f in prose() for t in texts if len(t) > 50 and t in (PKG / f).read_text()]
        self.assertEqual(copies, [])

    def test_an_agent_quotes_only_what_the_stack_has(self):
        ids = {r["id"] for r in policy.entries("the stack's", policy.read_toml(policy.INVARIANTS), POLICY, set())["invariant"]}
        ids |= {r["id"] for r in policy.entries("the stack's", policy.read_toml(policy.INVARIANTS), POLICY, set())["guidance"]}
        for f in tracked("agents/*.md"):
            for rid in re.findall(r"^\{\{rule ([a-z-]+)\}\}$", (PKG / f).read_text(), re.M):
                self.assertIn(rid, ids, f"{f} quotes {rid}, which a repo with no such entry could not render")

    def test_named_entries_render_one_bullet_each_in_order(self):
        self.assertEqual(policy.render(INV, ["scope-no-numbers", "issue-close"]).splitlines(),
                         ["- Bench numbers and timings stay out of commits and PR text.",
                          "- A `acme/app` issue is closed by hand only when one of our merged PRs fixed it, with a comment naming that PR: "
                          '`gh issue close <n> -R acme/app --comment "Fixed by #<pr>."`.'])
        with self.assertRaisesRegex(policy.Refused, "no invariant or guidance nope"):
            policy.render(INV, ["nope"])

    def test_an_entry_names_config_keys_and_the_reader_fills_them_in(self):
        for f in ("config/invariants.toml", "config/repos/acme/app/invariants.toml"):
            body = "\n".join(x for x in (PKG / f).read_text().splitlines() if not x.startswith("#"))
            self.assertEqual([f"{t!r} restates {k}" for k, t in restated(body, POLICY, repo=True)], [], f)
        full = policy.render(INV)
        self.assertNotIn("{", full)
        for want in ("waits for Robin, as does any PR labelled `needs-review`", "an issue labelled preapproved, or a task",
                     "Nothing labelled `hold` is started or merged", "by f-teammate gets a first review", "touches skyhook, vaultkey or sealing is"):
            self.assertIn(want, full)
        pol = json.loads(json.dumps(POLICY))
        pol["merge"].update(review_label="stop", preapproved_labels=["go", "ship"])
        pol["people"].update(owner="Sam", review_only=[])
        got = policy.render({"intro": "x", "guidance": [], "repo": [], "stack": policy.entries(
            "x", {"invariant": [{"id": "a", "text": "{merge.review_label} {merge.preapproved_labels:or} {people.owner} {people.review_only:or}",
                                 "enforced": "e", "test": "t"}]}, pol, set())["invariant"]})
        self.assertIn("- stop go or ship Sam no one\n", got)

    def test_a_brace_that_names_no_key_and_a_duplicate_id_are_refused(self):
        row = {"id": "a", "enforced": "e", "test": "t"}
        for said, why in (("see {merge.nope}", "no key merge.nope"), ("see {merge.preapproved_labels}", "needs a joiner"),
                          ("see {merge.repo:or}", "not one value"), ("see {merge}", "names no config"), ("a } b", "names no config")):
            with self.subTest(text=said), self.assertRaisesRegex(policy.Refused, why):
                policy.entries("x", {"invariant": [{**row, "text": said}]}, POLICY, set())
        with self.assertRaisesRegex(policy.Refused, "two entries have the id a"):
            policy.entries("x", {"invariant": [{**row, "text": "t"}], "guidance": [{"id": "a", "text": "t"}]}, POLICY, set())


class NoRestatedValues(unittest.TestCase):
    def test_skills_name_config_keys_instead_of_values(self):
        found = [f"{f}: {t!r} restates {k}" for f in prose() for k, t in restated((PKG / f).read_text(), POLICY)]
        self.assertEqual(found, [], "name the the config key instead of its value")

    def test_code_reads_config_keys_instead_of_values(self):
        found = [f"{f}: {t!r} restates {k}" for f in code() for k, t in restated((PKG / f).read_text(), POLICY, repo=True)]
        self.assertEqual(found, [], "read the value from the config (queue.py) instead of writing it again")

    def test_the_code_check_catches_what_the_code_said_before(self):
        old = {'"Your PR acme/app#{pr} is red at {head}"': "merge.repo", 'notes.append("ci-gate SUCCESS")': "merge.gate_check",
               "the diff touches sealing, Skyhook or Vaultkey": "patent.held_words"}
        for line, key in old.items():
            with self.subTest(line=line):
                self.assertIn(key, [k for k, _ in restated(line, POLICY, repo=True)])

    def test_the_check_catches_what_the_playbook_said_before(self):
        old = {"no progress for 2.5h": "run.idle_min",
               "issues assigned to octo": "people.me", "green ci-gate at a stable head": "merge.gate_check",
               "only real failures go to Robin": "people.owner",
               "Work on sealing, Skyhook and Vaultkey normally": "patent.held_words"}
        for line, key in old.items():
            with self.subTest(line=line):
                self.assertIn(key, [k for k, _ in restated(line, POLICY)])

    def test_dates_times_and_step_numbers_are_not_values(self):
        for line in ("12 Nov 12:47 SGT", "steps 0 to 5", "8. **Merge", "the 11 Nov retro", "`app-<n>-review-<head7>`", "resealing"):
            self.assertEqual(restated(line, POLICY), [], line)


class Harness(unittest.TestCase):
    PI = re.compile(r"@earendil-works/|pi-coding-agent")
    CLAUDE = re.compile(r"CLAUDE_PLUGIN_ROOT|tool_name|tool_input")
    PI_ADAPTERS = ("pi/",)
    CLAUDE_ADAPTERS = ("hooks/hooks.json", "hooks/guard.ts", ".claude-plugin/")

    def test_only_an_adapter_names_its_harness(self):
        wrong = []
        for f in tracked("queue.py", "pi/*.ts", "pi/**/*.ts", "hooks/*", "config/*", ".claude-plugin/*"):
            text = (PKG / f).read_text()
            if self.PI.search(text) and not f.startswith(self.PI_ADAPTERS):
                wrong.append(f"{f} imports pi, and is not a pi adapter")
            if self.CLAUDE.search(text) and not f.startswith(self.CLAUDE_ADAPTERS):
                wrong.append(f"{f} names Claude Code's hook input, and is not a Claude Code adapter")
        self.assertEqual(wrong, [])

    def test_each_adapter_calls_the_one_guard(self):
        for f in ("pi/rule-guard.ts", "hooks/guard.ts"):
            text = (PKG / f).read_text()
            self.assertRegex(text, r'from "(\./|\.\./hooks/)host\.ts"', f)
            self.assertNotIn("checkBash", text, f)


class Parts(unittest.TestCase):
    OTHERS = re.compile(r"(?<!usr/)(?<!homebrew/)(?<!#!/)bin/(?!everystate\b)[a-z][\w-]*|services/|dispatch/|extensions/"
                        r"|patent_push|wake\.py")

    def test_the_code_names_no_file_that_is_gone(self):
        wrong = [f"{f}: {m.group(0)}" for f in tracked("queue.py", "pi/*", "pi/**", "hooks/*", ".claude-plugin/*", "package.json")
                 for m in self.OTHERS.finditer((PKG / f).read_text())]
        self.assertEqual(wrong, [], "the queue, the config, the holds file and the wake are all queue.py")

    def test_both_manifests_load_only_the_stack(self):
        pi = json.loads((PKG / "package.json").read_text())["pi"]
        paths = [x for v in pi.values() for x in v]
        self.assertTrue(all(x.startswith(("./pi/", "./skills")) and (PKG / x).exists() for x in paths), paths)
        plugin = json.loads((PKG / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(set(plugin), {"name", "version", "description"})
        hooks = (PKG / "hooks" / "hooks.json").read_text()
        self.assertEqual(sorted(set(re.findall(r"CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", hooks))), ["hooks/guard.ts", "queue.py"])

    def test_the_check_catches_a_file_that_names_one(self):
        for gone in ('path.join(PKG, "bin/queue")', "services/wake.py", "bin/policy dump", "extensions/rule-guard/host.ts"):
            self.assertTrue(self.OTHERS.search(gone), gone)
        self.assertFalse(self.OTHERS.search("#!/bin/sh\npython3 queue.py invariants; queue.py list; bin/everystate; /opt/homebrew/bin/gh"))


class NoVerifiedComment(unittest.TestCase):
    def test_nothing_asks_for_the_verified_pr_comment(self):
        rx = re.compile(r"(?<!queue\.py )verified <|verified comment|verified-comment|`verified <sha>`|post (the|its) verdict on the PR", re.I)
        rendered = policy.render(INV)
        hits = [f"config/invariants.toml: {m.group(0)}" for m in rx.finditer(rendered)]
        for f in [*prose(), "README.md", "pi/README.md", "hooks/README.md"]:
            hits += [f"{f}: {m.group(0)}" for m in rx.finditer((PKG / f).read_text())]
        self.assertEqual(hits, [])
        self.assertIn("queue.py verified", next(r["text"] for r in INV["stack"] if r["id"] == "turn-ends-verified"))


if __name__ == "__main__":
    unittest.main()

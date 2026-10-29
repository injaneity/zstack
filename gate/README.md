# merge gate

One decision per open PR: `merge`, `ready`, `rerun`, `buffer`, `urgent`, `wait`, or `skip`, and a reason. The engine
came out of the onigiri tick unchanged; what used to be module constants for `trycua/cua` is now a JSON file per repo.

```
bin/gate.py --config gate/zstack.json                         decisions, changes nothing
bin/gate.py --config gate/zstack.json --holds h.txt --apply   undraft, rerun a red head once, merge
bin/gate.py --config gate/zstack.json --clone ../zstack --runs ../zstack
bin/gate.py --config gate/cua.json --score --score-limit 4    run Clef on PRs that have no score at their head
```

`--apply` refuses to run without a holds file, given either as `--holds` or as a `holds` key in the config. A path
that does not exist is an error, not "nothing is held": the tick opened its holds file directly and refused to merge
when it could not read it, and merging on an unreadable hold list is the one failure this gate may never have.
`--pause-file` (or a `pause_file` config key) stops every action and stops `--score` too, re-read just before
acting because scoring can run for twenty minutes. Both are re-read under a lock on the state file, so two
overlapping runs cannot rerun or merge the same PR twice.

The gate also checks `gh` is authenticated as the config's `me` before it does anything, since `me` filters which
PRs it looks at and the wrong account would gate someone else's.

`--clone` lets a verify PASS survive a merge of main into the branch, the way the tick did: it fetches
`refs/pull/N/head` and main into its own refs, then asks `engine.carry_pass` whether the head is a two-parent merge
whose first parent passed and whose tree equals a clean `git merge-tree` of its parents. `--runs` adds a `PASS <sha>`
line from a run's verdict.md.

Two things the gate will not do without evidence in the PR itself:

- **Move a PR with no evidence at the current head**: a `verify: PASS at <40-hex>` line, a `PASS <sha>` verdict from a
  run passed with `--runs`, or a PASS carried by `--clone`. That is `skip: no verify PASS at head`, and it is why
  zstack #1 reads as a skip today.
- **Merge on checks that all skipped.** An empty rollup is `wait`, never `green`. A repo with no CI therefore never
  reaches `merge`, which is why this repo has `.github/workflows/test.yml`: without it the gate could undraft a zstack
  PR but never land one.

## config

Every key is read by `engine.py`; a key the engine ignores fails a test in `tests/test_parity.py`.

| key | meaning |
| --- | --- |
| `repo` | `owner/name`, used for every `gh` and `git ls-remote` call |
| `me` | the login whose PRs the gate considers |
| `merge_method` | passed as `--squash`, `--merge` or `--rebase` |
| `label_prefix` | the label that carries a declared risk, e.g. `risk:` |
| `non_required_checks` | check names that never block, wait or rerun a merge |
| `preapproved` | PR numbers that skip Clef entirely |
| `clef_script` | risk classifier; omit and the repo never waits for a Clef score |
| `clef_cwd` | where Clef may read the repo while scoring; defaults to the caller's cwd |
| `sensitive` | regex: a high Clef score on a match promotes to `human` (urgent) |
| `outside_sensitive` | regex: the same, but only for outside contributors |
| `clef_required` | must a PR wait for a score at its head? Defaults to whether `clef_script` is set |
| `holds` | this repo's holds file, so `--apply` cannot be pointed at it without one |
| `pause_file` | this repo's pause file |

`gate/cua.json` is `trycua/cua` as the tick ran it. `gate/zstack.json` is the minimal one. A test asserts the first
equals the tick's own constants field by field.

## risk

Clef only ever escalates. `declared_risk` reads the `risk:` label and the last `risk: <word>` body line, and Clef's
tier is used only when the PR declares nothing. A Clef `deep` or `human` PR never merges without its owner unless it
is preapproved.

`gate/clef-risk.sh` pipes the PR into one read-only `pi` pass and reads back `Risk: low|standard|high`. It stays on
this box. It needs `pi` and about 10 seconds per PR, so `--score` caps a run with `--score-limit` (default 4) and
scores the rest next time. Drafts are not scored; the exception is a PR with a verify PASS at head, because the gate
cannot undraft it without a score.

## tests

```
python3 -m unittest discover -s tests
```

`tests/test_parity.py` compares this engine against 3,096 decisions captured from the tick: 35 real PR states times
every gate state, asserting identical `(action, reason)` pairs. The reason text is asserted too, because a reordered
rule usually changes both.

The expected decisions live in `tests/fixtures/expected.json.gz`, so the sweep runs in CI where no tick is checked
out, and it records the tick's SHA-256 and size so a capture from a modified tick cannot pass for a real one.
Regenerate only from an unmodified tick, with `python3 tests/freeze.py`; see `tests/fixtures/README.md`. When a tick
is present (`ONIGIRI_TICK`), one further test compares against it live so regeneration drift cannot hide.

What the sweep cannot tell you: it pins `merge_decision` and the rules it calls, and the two are close to copies of
each other. Everything that differs from the tick lives in `bin/gate.py` (holds, pause, the carry, the state keys,
`--set-risk`), and that code is covered by the CLI tests in the same file instead.

## what this is not

It is a merge pass, not the whole tick. Two things the tick did that this does not: `urgent` and `buffer` are a line
on stdout here, with no webhook and no buffer file, so nothing wakes anyone for a Clef-`human` PR; and a failed
merge exits 0, which a cron job will not notice. Wire those before pointing `--apply` at a repo unsupervised.

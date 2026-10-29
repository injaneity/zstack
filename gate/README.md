# merge gate

One decision per open PR: `merge`, `ready`, `rerun`, `buffer`, `urgent`, `wait`, or `skip`, and a reason. The engine
came out of the onigiri tick unchanged; what used to be module constants for `trycua/cua` is now a JSON file per repo.

```
bin/gate.py --config gate/cua.json            decide and print, move nothing
bin/gate.py --config gate/cua.json --apply    decide and act
```

Two flags, and everything else a repo needs is a key in its config. Scoring is not a mode: `--apply` scores the PRs
that need it, in the tick's order, and prints the decisions it made.

`--apply` refuses to run without a readable holds file named by the config's `holds` key. A missing path is an error,
not "nothing is held": the tick opened its holds file directly and refused to merge when it could not read it, and
merging on an unreadable hold list is the one failure this gate may never have. The config's `pause_file`, if any,
stops every action and stops scoring too, read once at the start like the tick did, so a pause written during a long
scoring pass takes effect on the next run rather than mid-pass.

Print mode moves no PR and writes no state. It does fetch each PR head and main into the checkout, because whether a
PASS would carry across a merge is part of the decision it is reporting, and the answer only exists in git.

Two overlapping runs are prevented by whatever schedules this: `cron-wrap.sh` runs each job under `flock -n`, so the
gate takes no lock of its own.

Every `gh` and `git` call runs with `GH_CONFIG_DIR` forced from `HOME`, so it uses the account logged in there rather
than a directory some other caller picked. Nothing checks which account that is: run it under the `HOME` that holds the
right login, as the tick did, and do not export `GH_TOKEN`, which overrides the config directory entirely. A missing
state file is a fresh start; a corrupt one stops the run, as it did in the tick, because reading it as empty would
rerun red PRs and re-buy scores it holds.

A verify PASS survives a merge of main into the branch, the way the tick did: the gate fetches `refs/pull/N/head` and
main into its own refs in the config's `checkout`, then asks `engine.carry_pass` whether the head is a
two-parent merge whose first parent passed and whose tree equals a clean `git merge-tree` of its parents. A `PASS <sha>`
line in a `verdict.md` under that checkout counts as evidence too. A fetch that fails records nothing and the
question is asked again next run, so a network blip does not strand the PR on "no verify PASS at head".

Two things the gate will not do without evidence in the PR itself:

- **Move a PR with no evidence at the current head**: a `verify: PASS at <40-hex>` line, a `PASS <sha>` verdict from a
  run, or a carried PASS. That is `skip: no verify PASS at head`, and it is why zstack #1 reads as a skip today.
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
| `clef_script` | risk classifier, run under `bash` in `checkout`; set, and a PR waits for a score at its head; omit and it never does |
| `sensitive` | regex: a high Clef score on a match promotes to `human` (urgent) |
| `holds` | this repo's holds file; `--apply` refuses to run without it |
| `pause_file` | this repo's pause file |
| `checkout` | this repo's clone: run verdicts are read from it, Clef runs in it, a PASS carry fetches into it |

`gate/cua.json` is `trycua/cua` as the tick ran it. `gate/zstack.json` is the minimal one, and it has no
`clef_script`: Clef scoring zstack PRs would be zstack gating itself, and a repo holding up its own merges on its own
classifier is the one queue it can never drain. A test asserts the first equals the tick's own constants field by
field.

## risk

Clef only ever escalates. `declared_risk` reads the `risk:` label and the last `risk: <word>` body line, and Clef's
tier is used only when the PR declares nothing. A Clef `deep` or `human` PR never merges without its owner unless it
is preapproved.

`gate/clef-risk.sh` pipes the PR into one read-only `pi` pass and prints the tier word. It stays on this box. It needs
`pi` and about 10 seconds per PR, so a pass scores at most four (`CLEF_MAX_PER_RUN`, the tick's `CLEF_MAX_PER_TICK`) and
the rest are scored next time. A scoring failure counts toward that cap and prints why, and it ends scoring for that
pass the way a timeout did in the tick: it spends the same 320s a score does, so an uncounted failure would retry the
whole repo every run. Decisions still run on the scores already held, so the PRs that could move still do. Drafts are not scored; the exception is a PR with a verify PASS at head in its body or
a `verdict.md`, because the gate cannot undraft it without a score. A PASS that only carried across a merge does not
count for that exception, which is the tick's rule: such a draft waits for its owner to mark it ready. To override a tier by hand, edit the PR's `risk:` label or add a
`risk: <word>` line to its body, and the gate reads it from there on the next pass.

## tests

```
python3 -m unittest discover -s tests
```

`tests/test_parity.py` compares this engine against 3,096 decisions captured from the tick: 35 real PR states times
every gate state, asserting identical `(action, reason)` pairs. The reason text is asserted too, because a reordered
rule usually changes both. A second set of tests drives `bin/gate.py` end to end through a temp config: the refusals,
the carry and its failed fetch, a mid-run error still saving, the pause, the once-only rerun, and scoring inside
`--apply`.

The expected decisions live in `tests/fixtures/expected.json.gz`, so the sweep runs in CI where no tick is checked
out, and it records the tick's SHA-256 and size so a capture from a modified tick cannot pass for a real one.
Regenerate only from an unmodified tick, with `python3 tests/freeze.py`; see `tests/fixtures/README.md`. When a tick
is present (`ONIGIRI_TICK`), one further test compares against it live so regeneration drift cannot hide.

## what this is not

It is a merge pass, not the whole tick. Three things the tick did that this does not: `urgent` and `buffer` are a
line on stdout here, with no webhook and no buffer file, so nothing wakes anyone for a Clef-`human` PR; a carry that
does not hold is retried silently instead of queueing a re-verify line in the backlog, so such a PR stays at `skip`
until its next push; and a failed merge exits 0, which a cron job will not notice. Wire those before pointing
`--apply` at a repo unsupervised.

# merge gate

One decision per open PR: `merge`, `ready`, `rerun`, `buffer`, `urgent`, `wait`, or `skip`, and a reason. The engine
came out of the onigiri tick unchanged; what used to be module constants for `trycua/cua` is now a JSON file per repo.

```
bin/gate.py --config gate/zstack.json                  decisions, changes nothing
bin/gate.py --config gate/zstack.json --apply          undraft, rerun a red head once, merge
bin/gate.py --config gate/zstack.json --clone ../zstack --runs ../zstack
```

`--clone` lets a verify PASS survive a merge of main into the branch, the way the tick did: it fetches
`refs/pull/N/head` and main into its own refs, then asks `engine.carry_pass` whether the head is a two-parent merge
whose first parent passed and whose tree equals a clean `git merge-tree` of its parents. `--runs` adds a `PASS <sha>`
line from a run's verdict.md.

Two things the gate will not do without evidence in the PR itself:

- **Move a PR with no evidence at the current head**: a `verify: PASS at <40-hex>` line, a `PASS <sha>` verdict from a
  run passed with `--runs`, or a PASS carried by `--clone`. That is `skip: no verify PASS at head`, and it is why
  zstack #1 reads as a skip today.
- **Merge on checks that all skipped.** An empty rollup is `wait`, never `green`. A repo with no CI therefore never
  reaches `merge`.

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
| `sensitive` | regex: a high Clef score on a match promotes to `human` (urgent) |
| `outside_sensitive` | regex: the same, but only for outside contributors |

`gate/cua.json` is `trycua/cua` as the tick ran it. `gate/zstack.json` is the minimal one. A test asserts the first
equals the tick's own constants field by field.

## risk

Clef only ever escalates. `declared_risk` reads the `risk:` label and the last `risk: <word>` body line, and Clef's
tier is used only when the PR declares nothing. A Clef `deep` or `human` PR never merges without its owner unless it
is preapproved.

`gate/clef-risk.sh` pipes the PR into one read-only `pi` pass and reads back `Risk: low|standard|high`. It stays on
this box. It needs `pi` and about 10 seconds per PR.

## tests

```
python3 -m unittest discover -s tests
```

`tests/test_parity.py` runs 35 real captured PR states through both the tick's `merge_decision` and this engine, over
every gate state (3,096 comparisons), and asserts identical `(action, reason)` pairs. It asserts the reason text too,
not just the action, because a reordered rule usually changes both. The tick is imported read-only from
`ONIGIRI_TICK`; set `ONIGIRI_TICK` to point at another copy.

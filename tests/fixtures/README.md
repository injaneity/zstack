# fixtures

Two files, both needed:

- `prs.json.gz` — 35 real PR states captured 2026-10-29.
- `expected.json.gz` — what the original tick decided for every one of them, in every gate state
  (3096 decisions). Frozen so the parity sweep runs in CI, where no tick is checked out.

## prs.json.gz

- 34 open `trycua/cua` PRs authored by `injaneity`: 11 drafts, 3 conflicting, pending checks, red checks, green checks
- `injaneity/zstack#1`, the draft this work exists for, and the only PR here with no
  checks at all

Fields: `repo`, `number`, `headRefOid`, `isDraft`, `mergeable`, `labels` (names only), `body`, `title`, `author`,
`state`, `updatedAt`, `reviewDecision`, `statusCheckRollup` (every check, unmodified).

Regenerate with `gh pr list` for the bulk (one call, all fields) and `gh pr view` for any single PR that needs
re-reading; the two return the same shape:

```bash
export HOME=/home/box GH_CONFIG_DIR=/home/box/.config/gh
gh pr list -R trycua/cua --state open --author injaneity --limit 100 \
  --json number,headRefOid,isDraft,mergeable,labels,body,title,author,state,updatedAt,reviewDecision,statusCheckRollup
gh pr view 1 -R injaneity/zstack --json number,headRefOid,isDraft,mergeable,labels,body,title,author,state,updatedAt,reviewDecision,statusCheckRollup
```

## expected.json.gz

Regenerate only against a tick **that has not been modified for the extraction**, so the expectations come from
production behaviour and never from this engine checking itself:

```bash
ONIGIRI_TICK=/path/to/unmodified/tick python3 tests/freeze.py
```

A diff in `expected.json.gz` is a deliberate behaviour change. Say which rule moved and why in the commit; don't
regenerate to make a red test go green.

Nothing here is secret: PR bodies, titles, public check names, public SHAs. No tokens, no webhook URL.

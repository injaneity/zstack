# fixtures

`prs.json.gz` is 35 real PR states captured 2026-10-29 with `gh pr view`, stored gzipped at full fidelity:

- 34 open `trycua/cua` PRs authored by `injaneity`: 11 drafts, 3 conflicting, pending checks, red checks, green checks
- `injaneity/zstack#1`, the draft this work exists for, and the only PR here with no checks at all

Fields: `repo`, `number`, `headRefOid`, `isDraft`, `mergeable`, `labels` (names only), `body`, `title`, `author`,
`state`, `updatedAt`, `reviewDecision`, `statusCheckRollup` (every check, unmodified).

Regenerate:

```bash
export HOME=/home/box GH_CONFIG_DIR=/home/box/.config/gh
gh pr list -R trycua/cua --state open --author injaneity --limit 100 \
  --json number,headRefOid,isDraft,mergeable,labels,body,title,author,state,updatedAt,reviewDecision,statusCheckRollup
gh pr view 1 -R injaneity/zstack --json number,headRefOid,isDraft,mergeable,labels,body,title,author,state,updatedAt,reviewDecision,statusCheckRollup
```

Nothing here is secret: PR bodies, titles, public check names, public SHAs. No tokens, no webhook URL.

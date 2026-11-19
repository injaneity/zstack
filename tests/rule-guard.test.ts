import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import { checkBash, checkWrite, mergeOwned, prTextProblems } from "../hooks/rules.ts";

for (const k of ["ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"]) delete process.env[k];

const DUMP = () => execFileSync("python3", ["-I", new URL("../queue.py", import.meta.url).pathname, "dump"], { encoding: "utf8" });
const OWNED = mergeOwned(DUMP);
const ISSUES = OWNED;
const repoAt = (dir: string) => (dir.startsWith("/app") ? "acme/app" : dir.startsWith("/zs") ? "injaneity/zstack" : dir.startsWith("/scratch") ? null : undefined);
const host = { repoAt, labels: ["hold", "needs-review", "preapproved"] };
const blocked = (c: string, cwd = "/zs") => assert.match(checkBash(c, OWNED, host, cwd) ?? "", /^blocked: /, c);
const allowed = (c: string, cwd = "/zs") => assert.equal(checkBash(c, OWNED, host, cwd), undefined, c);
const inApp = "/app/wt";

test("force-pushes and hook skips are blocked however they are written", () => {
	for (const c of [
		"git push --force origin main",
		"git push -f",
		"git push -uf origin x",
		"git push --force-with-lease",
		"git push --force-with-lease=main:abc origin main",
		"git push origin +main",
		"git push --no-verify origin x",
		"git -C wt push --force",
		"git -c core.hooksPath=/dev/null push origin x",
		"git -c alias.send=push send origin main",
		"git -calias.send='push -f' send",
		"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.send GIT_CONFIG_VALUE_0=push git send",
		"cd wt && git push -f",
		"env GIT_TRACE=1 git push -f",
		"/usr/bin/git push --force",
		'ssh host "git push --force"',
		"~/onigiri-vm/vm 'cd /srv/x && git push -f origin y'",
		'bash -c "git push --no-verify"',
		"sudo -u bob git push -f",
		"env -u X git push -f",
		"nice -n 5 git push --force",
		"timeout -k 5 60 git push -f",
		"sudo --user bob git push -f",
		"env --unset X git push -f",
		"git -ccore.hooksPath=/dev/null push",
		"git --config-env=core.hooksPath=HP push",
		"GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git push",
		"git --git-dir /tmp/repo push -f",
		"{ git push -f; }",
		"if true; then git push -f; fi",
		"while false; do git push -f; done",
		"! git push --force",
		"env -i git push -f",
		"env -i PATH=/usr/bin git push -f",
		"perl -e 'alarm 20; exec @ARGV' git push -f origin x",
		"perl -e 'alarm shift; exec @ARGV' 300 git push --no-verify",
	])
		blocked(c);
});

test("ordinary pushes and git commands run", () => {
	for (const c of ["git add -- -calias.notes", "git -c user.name=x commit -m 'fix: x'", "git -c core.pager=cat log", "git config alias.x y", "git commit -m 'alias.send=push'"])
		allowed(c);
	for (const c of [
		"git push",
		"git push -u origin fix/x",
		"git push origin HEAD:refs/heads/x",
		"git -C wt push app-private fix/x",
		"git log --format=%H -1",
		"git fetch --force origin",
		'git commit -m "never git push -f"',
		"echo git push --force",
		"git -c core.hooksPath=/dev/null status",
	])
		allowed(c);
});

test("merging in a merge-job repo is blocked", () => {
	blocked("gh pr merge 7031 -R acme/app --squash");
	blocked("gh pr merge 7031 --repo=acme/app --auto");
	blocked("gh pr merge https://github.com/acme/app/pull/7031");
	blocked("gh pr merge 7031 --squash", inApp);
	blocked("gh api -X PUT repos/acme/app/pulls/7031/merge");
	blocked("GH_CONFIG_DIR=x gh pr merge 1 -R Acme/App");
	blocked("gh pr merge 1", "/tmp/x");
	blocked("cd /app/wt && gh pr merge 1 --squash");
	blocked("cd ~/work/app && gh pr merge 1");
	blocked("gh api -X PUT repos/{owner}/{repo}/pulls/1/merge", inApp);
	blocked("gh api repos/acme/app/pulls/1/merge -f merge_method=squash");
});

test("approving a fork's workflow runs in a merge-job repo runs; a merge beside it stays blocked", () => {
	for (const c of ["gh api -X POST repos/acme/app/actions/runs/9/approve", "gh api --method POST repos/acme/app/actions/runs/$RUN/approve",
		"gh api -X POST repos/{owner}/{repo}/actions/runs/9/approve", "gh api -X POST /repos/acme/app/actions/runs/9/approve"]) {
		allowed(c);
		allowed(c, inApp);
		allowed(c, "/tmp/x");
	}
	blocked("gh api -X POST repos/acme/app/actions/runs/9/approve && gh pr merge 1 -R acme/app");
	blocked("gh api -X POST repos/acme/app/actions/runs/9/approve; gh api -X PUT repos/acme/app/pulls/1/merge", "/scratch");
});

test("CI reruns in a merge-job repo run, whatever the run's conclusion: the session decides them", () => {
	for (const c of ["gh run rerun 9 -R acme/app --failed", "gh run rerun 9 -R acme/app", "gh run rerun --job 90 -R acme/app",
		"gh run rerun 9 --job=90 -R acme/app", "gh run rerun $RUN -R acme/app", "gh run rerun 9 --failed",
		"gh api -X POST repos/acme/app/actions/runs/9/rerun-failed-jobs", "gh api -X POST repos/acme/app/actions/jobs/90/rerun",
		"gh api -X POST repos/{owner}/{repo}/actions/runs/9/rerun"]) {
		allowed(c);
		allowed(c, inApp);
		allowed(c, "/tmp/x");
	}
	assert.equal(checkBash("gh run rerun 9 --failed", OWNED, { ...host, ghRepo: "acme/app" }, "/scratch"), undefined);
	blocked("gh run rerun 9 -R acme/app && gh pr merge 1 -R acme/app");
	blocked("gh api -X PUT -f x=repos/acme/app/actions/runs/9/rerun repos/acme/app/pulls/1/merge", "/scratch");
	blocked("gh api -H x/repos/other/repo/pulls/1/merge -X PUT repos/acme/app/pulls/1/merge", "/scratch");
	blocked("gh pr merge 1 --squash -b https://github.com/other/repo/pull/1", inApp);
	const otherEnv = { ...host, ghRepo: "acme/other" };
	for (const c of ["gh api -X PUT repos/{owner}/app/pulls/1/merge", "gh api -X PUT repos/acme/{repo}/pulls/1/merge"])
		assert.match(checkBash(c, OWNED, c.includes("{repo}") ? { ...host, ghRepo: "other/app" } : otherEnv, "/scratch") ?? "", /^blocked: /, c);
	assert.equal(checkBash("gh api -X PUT repos/{owner}/zstack/pulls/1/merge", OWNED, otherEnv, "/scratch"), undefined);
	blocked("gh api -X PUT repos/{owner}/{bogus}/pulls/1/merge", inApp);
});

test("merges elsewhere run, and nothing is owned unless configured", () => {
	allowed("gh pr merge 33 -R injaneity/zstack --squash");
	allowed("gh pr merge 33 --squash");
	allowed("gh pr view 7031 -R acme/app --json state", inApp);
	allowed("gh pr checks 7031 -R acme/app", inApp);
	allowed("gh api repos/acme/app/pulls/123/merge");
	blocked("cd /zs/wt || gh pr merge 1", inApp);
	blocked("cd /zs/wt && gh pr merge 33", inApp);
	blocked("GH_REPO=acme/app gh pr merge 1");
	blocked("{ gh pr merge 1 -R acme/app; }");
	blocked("{ cd /app; gh pr merge 1; }");
	blocked("if cd /app; then gh pr merge 1; fi");
	blocked("if true; then gh pr merge 1; fi", inApp);
	blocked("gh pr merge 1 -R github.com/acme/app");
	blocked("gh pr merge 1 --repo=https://github.com/acme/app.git");
	blocked("gh api repos/acme/app/pulls/1/merge -f x=y -X PUT");
	allowed("cd /zs/wt && gh pr merge 33 -R injaneity/zstack", inApp);
	allowed("gh api repos/acme/app/pulls/1/merge -f x=y -X GET");
	allowed("gh pr merge 33 -R injaneity/zstack", inApp);
	allowed("gh pr merge 1", "/scratch");
	assert.match(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "acme/app" }, "/scratch") ?? "", /^blocked: /);
	assert.equal(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "injaneity/zstack" }, "/scratch"), undefined);
	const zsEnv = { ...host, ghRepo: "injaneity/zstack" };
	assert.equal(checkBash("gh pr merge 1", OWNED, zsEnv, inApp), undefined);
	for (const c of ["GH_REPO= gh pr merge 1", "env -u GH_REPO gh pr merge 1", "env --unset=GH_REPO gh pr merge 1", "env -i gh pr merge 1"])
		assert.match(checkBash(c, OWNED, zsEnv, inApp) ?? "", /^blocked: /, c);
	assert.match(checkBash("GH_REPO=acme/app bash -c 'gh pr merge 1'", OWNED, zsEnv, "/scratch") ?? "", /^blocked: /);
	assert.match(checkBash("env -u GH_REPO sh -c 'gh pr ready 1'", OWNED, zsEnv, inApp) ?? "", /^blocked: /);
	blocked("gh pr merge 1 -R acme/app --subject --help");
	blocked("gh pr merge 1 -R acme/app --body --disable-auto");
	assert.match(checkBash("ssh server 'gh pr merge 123'", OWNED, zsEnv, "/scratch") ?? "", /^blocked: /);
	allowed("gh pr merge 1 -R acme/app --squash --help");
	allowed("gh pr merge 1 -R acme/app --squash --disable-auto");
	allowed("gh pr merge --help", inApp);
	allowed("gh pr merge 6474 -R acme/app --disable-auto");
	assert.equal(checkBash("gh pr merge 7031 -R acme/app", new Set(), host, inApp), undefined);
});

test("readying a draft in a merge-job repo is blocked, through gh or GraphQL", () => {
	blocked("gh pr ready 7040 -R acme/app");
	blocked("gh pr ready 7040", inApp);
	blocked("gh pr ready https://github.com/acme/app/pull/7040");
	blocked(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -F query=@m.graphql -f q='enablePullRequestAutoMerge'`);
	blocked(`gh api -X POST graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	allowed("gh pr ready 7040 -R acme/app --undo");
	allowed("gh pr ready 33 -R injaneity/zstack", inApp);
	allowed(`gh api graphql -f query='{ repository(owner:"acme",name:"app"){ pullRequest(number:1){ isDraft } } }'`);
	allowed("gh pr create --draft --title 'fix: x' --body y", inApp);
	allowed("gh pr view 7040 --json isDraft", inApp);
});

test("here-document bodies are data unless a shell reads them", () => {
	allowed("cd /zs && python3 - <<'EOF'\nsub('docs', 'never gh pr merge 1 or git push -f')\nEOF");
	allowed("cat > /tmp/body.md <<EOF\nwe never run gh pr merge here\nEOF\ngh pr create --body-file /tmp/body.md", inApp);
	blocked("python3 - <<'EOF'\nx\nEOF\ngit push -f");
	blocked("bash <<'EOF'\ngit push --force\nEOF");
	blocked("~/onigiri-vm/vm <<'EOF'\ngit push -f\nEOF");
	blocked("cat <<EOF\nx"+"\ngit push -f");
	blocked("echo '<<EOF'\ngit push -f\nEOF");
	blocked("cat <<EOF\n$(git push -f)\nEOF");
	blocked("echo hi # <<EOF\ngit push --force\nEOF");
	blocked("cat <<'EOF' | bash\ngit push -f\nEOF");
	blocked("cat <<EOF.txt\ndata\nEOF.txt\ngit push -f\nEOF");
	blocked("cat <<EOF \\\n | bash\ngit push -f\nEOF");
	blocked("echo \\<<EOF\ngit push -f\nEOF");
	blocked("cat <<'EOF' | sh -s\ngh pr merge 1 -R acme/app\nEOF");
	allowed("cat <<'EOF'\n$(git push -f)\nEOF");
});

test("the labels the queue reads are the owner's: a thread never adds, removes or changes one", () => {
	for (const c of ["gh pr edit 3 -R acme/app --remove-label hold", "gh issue edit 3 --remove-label=hold", "gh issue edit 3 -R acme/app --add-label bug,Preapproved",
		"gh pr create -R acme/app --title 'fix: x' --body y -l needs-review", "gh issue create -R acme/app --title x --label preapproved", "gh pr edit 3 --add-label $L",
		"gh label delete hold -R acme/app --yes", "gh label edit hold --name later", "gh label create preapproved", "gh pr -R acme/app edit 3 --remove-label hold",
		"gh api -X DELETE repos/acme/app/issues/3/labels/hold", "gh api repos/acme/app/issues/3/labels -f 'labels[]=preapproved'",
		"gh api -X PUT repos/acme/app/issues/3/labels --input l.json", "bash -c 'gh issue edit 3 --remove-label hold'",
		"gh api -X PATCH repos/acme/app/issues/12 -f labels[]=preapproved", "gh api -X PATCH repos/acme/app/issues/12 -F 'labels[0]=bug'",
		`gh api -X PATCH repos/acme/app/issues/12 --raw-field '{"labels": []}'`,
		`gh api graphql -f query='mutation { addLabelsToLabelable(input: {labelableId: "I", labelIds: ["L"]}) { clientMutationId } }'`,
		`gh api graphql -f query='mutation { removeLabelsFromLabelable(input: {labelableId: "I", labelIds: ["L"]}) { clientMutationId } }'`,
		`gh api graphql -f query='mutation { updateIssue(input: {id: "I", labelIds: []}) { clientMutationId } }'`])
		blocked(c, inApp);
	assert.match(checkBash("gh pr edit 3 --remove-label hold", OWNED, host, inApp) ?? "", /the label `hold`, which only the owner sets \(the invariant own-controls\)/);
	for (const c of ["gh pr edit 3 --add-label bug", "gh issue list --label hold", "gh pr list -l needs-review", "gh label list", "gh api repos/acme/app/issues/3/labels",
		"gh api -X PATCH repos/acme/app/issues/12 -f title=labels", "gh api -X PATCH repos/injaneity/zstack/issues/12 -f labels[]=preapproved",
		`gh api graphql -f query='{ repository(owner:"acme",name:"app"){ labels(first:5){ nodes { name } } } }'`,
		"gh pr edit 3 -R injaneity/zstack --remove-label hold", "gh pr edit 3 --title 'fix: hold the lock'"])
		allowed(c, inApp);
	assert.match(checkBash("gh pr edit 3 --add-label bug", OWNED, { ...host, labels: null }, inApp) ?? "", /the labels the queue reads are unknown/);
});

test("the owned repos are every repo the config has, and an unreadable config refuses every merge and ready", () => {
	assert.deepEqual(OWNED, new Set(["acme/app"]));
	assert.deepEqual(mergeOwned(() => '{"repos": ["Acme/App", "acme/tool"]}'), new Set(["acme/app", "acme/tool"]));
	for (const dump of [() => { throw new Error("policy: missing merge.repo"); }, () => "not json", () => "{}", () => '{"repos": ["app"]}', () => '{"repos": []}', () => '{"merge": {"repo": "acme/app"}}'])
		assert.equal(mergeOwned(dump), null);
	for (const c of ["gh pr merge 33 -R injaneity/zstack --squash", "gh pr ready 1", "gh api -X PUT repos/x/y/pulls/1/merge",
		"gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'"])
		assert.match(checkBash(c, null, host, "/zs") ?? "", /queue\.py dump failed/, c);
	for (const c of ["gh pr view 1", "gh run rerun 9 --failed", "gh pr merge 1 --help", "git push"])
		assert.equal(checkBash(c, null, host, "/zs"), undefined, c);
});

test("closing a acme/app issue by hand needs a comment naming the PR that fixed it", () => {
	const no = (c: string, cwd = "/zs", h = host) => assert.match(checkBash(c, OWNED, h, cwd, ISSUES) ?? "", /^blocked: .*issue-close/, c);
	const ok = (c: string, cwd = "/zs", h = host) => assert.equal(checkBash(c, OWNED, h, cwd, ISSUES), undefined, c);
	assert.deepEqual(ISSUES, new Set(["acme/app"]));
	for (const c of [
		'gh issue close 6853 -R acme/app --comment "Fixed by #6928."',
		"gh issue close 6853 -R acme/app -c 'Fixed by #6928'",
		'gh issue close 6853 -R acme/app --comment="Fixed by https://github.com/acme/app/pull/6928"',
		'gh issue close https://github.com/acme/app/issues/6853 -c "Fixed by #6928" --reason completed',
		'gh pr close 3138 -R acme/app --comment "Replaced by #7587, which merged."',
		"gh issue close --help",
		"gh issue view 6853 -R acme/app",
		"gh issue comment 6853 -R acme/app --body 'x'",
		"gh pr close 1 -R injaneity/zstack",
	]) ok(c);
	ok('gh issue close 6853 --comment "Fixed by #6928"', inApp);
	for (const c of [
		"gh issue close 6853 -R acme/app",
		'gh issue close 6853 -R acme/app --comment "fixed on main"',
		'gh issue close 6853 -R acme/app --comment "see issue&#35;1"',
		'gh issue close 6853 -R acme/app --comment "Fixed by #6928" --reason "not planned"',
		'gh issue close 6853 -R acme/app --comment "Fixed by #6928" -r duplicate',
		'gh issue close 6853 -R acme/app --duplicate-of 6000 --comment "dup of #6000"',
		"gh issue close https://github.com/acme/app/issues/6853",
		'gh issue close 6853 -R acme/app --comment "$MSG"',
		"gh api -X PATCH repos/acme/app/issues/6853 -f state=closed",
		`gh api repos/acme/app/issues/6853 --input - <<< '{"state": "closed"}' -X PATCH`,
		`gh api graphql -f query='mutation { closeIssue(input: {issueId: "X"}) { clientMutationId } }'`,
		"gh api -X PATCH repos/acme/app/issues/5 --input payload.json",
		"gh api repos/acme/app/issues/5 --input=- -X PATCH",
		"gh api -X PATCH 'repos/acme/app/issues/5?x=/a' --input payload.json",
		"gh api -X PATCH 'repos/acme/app/issues/5#/a' -f state=closed",
		"gh api -X PATCH '/repos/acme/app/issues/5/?a=b' -f state=closed",
		'ssh vm "gh issue close 6853 -R acme/app"',
		"GH_REPO=acme/app gh issue close 6853",
	]) no(c);
	no("gh issue close 6853", inApp);
	no("cd /app/wt && gh issue close 6853");
	no("gh issue close 6853", "/zs", { ...host, ghRepo: "acme/app" });
	ok("gh issue close 12 -R injaneity/zstack");
	ok("gh issue close 12", "/scratch");
	ok("gh api -X PATCH repos/injaneity/zstack/issues/12 -f state=closed");
	ok("gh api -X PATCH repos/acme/app/issues/6853 -f title=x");
	ok("gh api -X POST repos/acme/app/issues/6853/comments --input body.json");
	no("gh issue close 12", "/tmp/x");
	assert.match(checkBash("gh issue close 12 -R injaneity/zstack", OWNED, host, "/zs", null) ?? "", /^blocked: /);
	assert.equal(checkBash("gh issue close 6853 -R acme/app", OWNED, host, "/zs", new Set()), undefined);
});

test("a read-only item's thread pushes nothing and posts nothing, and reads freely", () => {
	const ro = { ...host, readonly: () => true as boolean | undefined };
	const no = (c: string, h = ro) => assert.match(checkBash(c, OWNED, h, inApp) ?? "", /^blocked: a push or a GitHub write from a read-only item/, c);
	for (const c of ["git push origin x", "git -C wt push", "gh pr comment 7030 --body x", "gh pr review 7030 --approve", "gh pr close 7030", "gh issue close 7030 -c 'Fixed by #1.'",
		"gh pr create --title 'fix: x' --body y", "gh pr edit 7030 --title 'fix: y'", "gh issue comment 7030 -b x", "gh api -X POST repos/acme/app/issues/7030/comments -f body=x",
		"gh api graphql -f query='mutation { addComment(input: {}) { clientMutationId } }'", "gh pr -R acme/app comment 7030 -b x", "bash -c 'git push'", "gh pr create -h feature --title 'fix: x' --body y", "gh pr comment 7030 --help"])
		no(c);
	for (const cwd of ["/zs", "/tmp/elsewhere"])
		assert.match(checkBash("cd /tmp && gh pr comment 3 -R acme/app -b hi", OWNED, ro, cwd) ?? "", /^blocked: a push or a GitHub write from a read-only item/, cwd);
	for (const c of ["gh pr view 7030 --json files", "gh pr diff 7030", "gh pr checkout 7030", "gh pr list", "gh pr checks 7030", "gh api repos/acme/app/pulls/7030/files",
		"gh run view 9 --log", "gh search prs x", "git fetch origin", "git log -3", "git commit -m 'fix: x'"])
		assert.equal(checkBash(c, OWNED, ro, inApp), undefined, c);
	no("git push origin x", { ...host, readonly: () => undefined });
	for (const h of [host, { ...host, readonly: () => false as boolean | undefined }])
		assert.equal(checkBash("gh pr comment 7030 --body x", OWNED, h, inApp), undefined);
});

test("a thread never touches the queue's file", () => {
	for (const c of ["cat ~/zstack/acme/app/queue.json", "python3 -c 'open(\"/r/queue.json\", \"w\")'", "echo {} > /r/queue.json", "jq .verdicts /r/queue.json", "rm queue.json"])
		assert.match(checkBash(c, OWNED, host, inApp) ?? "", /^blocked: it names the queue's file/, c);
	allowed("cat /r/queue.json", "/zs");
	allowed("./queue.py list", inApp);
	for (const f of ["/r/queue.json", "queue.json", "@/r/queue.json"]) assert.match(checkWrite(f) ?? "", /^blocked: it writes the queue's file/);
	for (const f of ["/r/queue.json.md", "/r/myqueue.json", "queue.py", ""]) assert.equal(checkWrite(f), undefined, f);
});

test("a PR title or body that breaks the repo invariant text is refused in the merge job's repo, and nowhere else", () => {
	const files: Record<string, string> = { "/app/wt/body.md": "## Summary\nx\n\n🤖 Generated with [Some Tool](https://example.com/tool)\n", "/app/wt/ok.md": "## Summary\nthe scoped repository checks passed\nrisk: light\n" };
	const h = { ...host, readText: (f: string, d: string | undefined) => files[path.posix.resolve(d ?? "/", f)] };
	const no = (c: string, why: RegExp, cwd = inApp) => assert.match(checkBash(c, OWNED, h, cwd) ?? "", why, c);
	const ok = (c: string, cwd = inApp) => assert.equal(checkBash(c, OWNED, h, cwd), undefined, c);
	no('gh pr create --title "Fix the driver" --body "x"', /^blocked: a PR title that is not Conventional Commit/);
	no('gh pr create -R acme/app -t "fix: x" -b "built in /home/box/work/app"', /local machine path/, "/zs");
	no('gh pr edit 12 --body "x\nGenerated with [A Tool](https://a.example)"', /"Generated with" line for a tool/);
	ok('gh pr edit 12 --body "x\nverify: the report is generated with care"');
	no('gh pr create --title "fix(driver): x" --body "x\n\nCo-authored-by: A <a@b.c>"', /Co-authored-by/);
	no("gh pr create --title 'fix(driver): x' --body-file body.md", /"Generated with" line/);
	no('gh pr create --title="refactor x" -F ok.md', /Conventional Commit/);
	ok('gh pr create --title "fix(driver): keep the window focused" --body-file ok.md');
	ok('gh pr create --title "feat!: drop the old flag" --body "risk: light"');
	ok('gh pr create --title "$TITLE" --body-file missing.md');
	ok("gh pr create --fill");
	ok("gh pr edit 12 --add-label bug");
	ok('gh pr create --title "Fix the driver" --body "/Users/me/x"', "/zs");
	ok('gh pr view 12 --json title');
	assert.deepEqual(prTextProblems("docs(readme): say where ~/Library/x and /usr/bin live", "see https://example.com/home/page and /usr/local/bin/x"), []);
});

test("a merge in an owned repo runs only as the gated command, and only when the queue's gate opens", () => {
	const H = "a".repeat(40), asked: [string, number, string][] = [];
	const gated = { ...host, gate: (repo: string, pr: number, head: string) => { asked.push([repo, pr, head]); return pr === 40 ? undefined : `queue: PR ${pr} does not merge: it has a hold row`; } };
	const run = (c: string) => checkBash(c, OWNED, gated, "/app");
	assert.equal(run(`gh pr merge 40 --squash --match-head-commit ${H}`), undefined);
	assert.equal(run(`gh pr merge 40 -R acme/app --match-head-commit ${H} --squash`), undefined);
	assert.match(run(`gh pr merge 41 --squash --match-head-commit ${H}`) ?? "", /^blocked: queue: PR 41 does not merge: it has a hold row/);
	assert.deepEqual(asked, [["acme/app", 40, H], ["acme/app", 40, H], ["acme/app", 41, H]]);
	for (const c of [`gh pr merge 40 --squash`, `gh pr merge --squash --match-head-commit ${H}`, `gh pr merge 40 --match-head-commit ${H}`,
		`gh pr merge 40 --squash --auto --match-head-commit ${H}`, `gh pr merge 40 --squash --admin --match-head-commit ${H}`,
		`gh pr merge 40 --squash --match-head-commit abc123`, `gh pr merge https://github.com/acme/app/pull/40 --squash --match-head-commit ${H}`,
		`gh api -X PUT repos/acme/app/pulls/40/merge`, `gh pr merge 40 --squash --match-head-commit ${H} --match-head-commit ${"b".repeat(40)}`,
		`gh pr merge 40 --squash --auto=true --match-head-commit ${H}`, `gh pr merge 40 --squash --admin=true --match-head-commit ${H}`,
		`gh pr merge 40 41 --squash --match-head-commit ${H}`, `gh pr merge 40 -R other/repo -R acme/app --squash --match-head-commit ${H}`,
		`gh pr merge 40 --squash --match-head-commit=${H}`, `gh pr merge 40 --squash --rebase --match-head-commit ${H}`,
		`gh pr merge 40 --squash --match-head-commit ${H} --body x`])
		assert.match(run(c) ?? "", /^blocked: /, c);
	for (const c of [`printf '%s %s' --match-head-commit ${"b".repeat(40)} | xargs gh pr merge 40 --squash --match-head-commit ${H}`,
		`env GH_DEBUG=1 gh pr merge 40 --squash --match-head-commit ${H}`, `gh pr -R acme/app merge 40 --auto`, `gh pr -R acme/app merge 40 --squash --match-head-commit ${H}`,
		`gh alias set m 'pr merge'`, `gh m 40 --auto -R acme/app`, `gh alias import aliases.yml`, "gh co 40",
		"gh api graphql --input mutation.json", "gh api graphql -F query=@mutation.graphql", "cat m.json | gh api graphql --input -"])
		assert.match(run(c) ?? "", /^blocked: /, c);
	for (const c of ["gh alias list", "gh pr view 40", "gh pr -R acme/app view 40", "gh run rerun 9 --failed", "gh m --help", "gh api graphql -f query='query { viewer { login } }'"])
		assert.equal(run(c), undefined, c);
	assert.equal(run(`gh pr merge 40 -s -d --match-head-commit ${H}`), undefined);
	asked.pop();
	assert.equal(asked.length, 3);
	assert.match(checkBash(`gh pr merge 40 --squash --match-head-commit ${H}`, OWNED, host, "/app") ?? "", /^blocked: /);
});

test("a coding thread reports to the queue but never moves it, and never marks a head verified", () => {
	const verdict = "ZSTACK_CONFIG=/c ZSTACK_REPO=acme/app python3 /pkg/zstack/queue.py verified app-1 40 abc auto";
	const inThread = { ...host, thread: "t-9" };
	for (const c of ["./queue.py pass", "/pkg/zstack/queue.py pass", verdict, "queue.py release app-2",
		"python3 queue.py add x", "cd /tmp && /pkg/zstack/queue.py add y --preapproved", "python3 -I ../zstack/queue.py pass",
		"gh api -X POST repos/acme/app/statuses/abc -f state=success -f context=zstack/verified",
		"gh api repos/acme/app/check-runs -f name=zstack/verified -f head_sha=abc"])
		blocked(c, inApp);
	for (const c of ["/pkg/zstack/queue.py release", "queue.py release --hook", "queue.py gate 40 abc", "queue.py list", "queue.py invariants",
		"queue.py seat verifier", "gh api repos/acme/app/statuses/abc", "gh api repos/acme/app/commits/abc/check-runs", "ls queue.py", "git add queue.py"])
		allowed(c, inApp);
	for (const c of ["./queue.py pass", "queue.py verified app-1 40 abc auto",
		"gh api -X POST repos/acme/app/statuses/abc -f state=success -f context=zstack/verified"])
		allowed(c, "/zs");
	assert.match(checkBash("./queue.py pass", OWNED, host, inApp) ?? "", /only the machine's scheduled pass and the orchestrator move the queue/);
	assert.equal(checkBash(verdict, OWNED, inThread, inApp), undefined);
	assert.equal(checkBash("echo $T3CODE_THREAD_ID", OWNED, inThread, inApp), undefined);
	for (const c of [`T3CODE_THREAD_ID=t-0 ${verdict}`, `env -u T3CODE_THREAD_ID ${verdict}`, `unset T3CODE_THREAD_ID; ${verdict}`,
		`export T3CODE_THREAD_ID=t-0 && ${verdict}`, "./queue.py pass"])
		assert.match(checkBash(c, OWNED, inThread, inApp) ?? "", /^blocked: /, c);
});

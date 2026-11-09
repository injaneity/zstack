// Tests for extensions/rule-guard/rules.ts. Run: node --test tests/rule-guard.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkBash, checkWrite, mergeOwned } from "../extensions/rule-guard/rules.ts";

const OWNED = mergeOwned("trycua/cua");
// a fake `git remote get-url origin`: any directory under /cua is a trycua/cua checkout
// and /scratch is a directory outside any checkout
const repoAt = (dir: string) => (dir.startsWith("/cua") ? "trycua/cua" : dir.startsWith("/zs") ? "injaneity/zstack" : dir.startsWith("/scratch") ? null : undefined);
// and a fake filesystem whose only holds files are these
const FILES = new Set(["/srv/onigiri/hold-prs.txt", "/zs/sync/hold-prs.txt"]);
const host = { repoAt, isFile: (p: string) => FILES.has(p) };
const blocked = (c: string, cwd = "/zs") => assert.match(checkBash(c, OWNED, host, cwd) ?? "", /^blocked: /, c);
const allowed = (c: string, cwd = "/zs") => assert.equal(checkBash(c, OWNED, host, cwd), undefined, c);
const inCua = "/cua/wt";

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
	for (const c of [
		"git push",
		"git push -u origin fix/x",
		"git push origin HEAD:refs/heads/x",
		"git -C wt push cua-staging fix/x",
		"git log --format=%H -1",
		"git fetch --force origin",
		'git commit -m "never git push -f"',
		"echo git push --force",
		"git -c core.hooksPath=/dev/null status",
	])
		allowed(c);
});

test("merging or rerunning CI in a merge-job repo is blocked", () => {
	blocked("gh pr merge 7031 -R trycua/cua --squash");
	blocked("gh pr merge 7031 --repo=trycua/cua --auto");
	blocked("gh pr merge https://github.com/trycua/cua/pull/7031");
	blocked("gh pr merge 7031 --squash", inCua);
	blocked("gh run rerun 123 -R trycua/cua --failed");
	blocked("gh api -X PUT repos/trycua/cua/pulls/7031/merge");
	blocked("GH_CONFIG_DIR=x gh pr merge 1 -R TryCua/Cua");
	blocked("gh pr merge 1", "/tmp/x");
	blocked("cd /cua/wt && gh pr merge 1 --squash");
	blocked("cd ~/onigiri/cua && gh pr merge 1");
	blocked("gh api -X PUT repos/{owner}/{repo}/pulls/1/merge", inCua);
	blocked("gh api repos/trycua/cua/pulls/1/merge -f merge_method=squash");
	blocked("gh api -X POST repos/trycua/cua/actions/runs/9/rerun-failed-jobs");
});

test("merges elsewhere run, and nothing is owned unless configured", () => {
	allowed("gh pr merge 33 -R injaneity/zstack --squash");
	allowed("gh pr merge 33 --squash");
	allowed("gh pr view 7031 -R trycua/cua --json state", inCua);
	allowed("gh pr checks 7031 -R trycua/cua", inCua);
	allowed("gh api repos/trycua/cua/pulls/123/merge");
	blocked("cd /zs/wt || gh pr merge 1", inCua);
	blocked("cd /zs/wt && gh pr merge 33", inCua); // a cd can fail, so the starting checkout still counts
	blocked("GH_REPO=trycua/cua gh pr merge 1");
	blocked("{ gh pr merge 1 -R trycua/cua; }");
	blocked("{ cd /cua; gh pr merge 1; }");
	blocked("if cd /cua; then gh pr merge 1; fi");
	blocked("if true; then echo x >> hold-prs.txt; fi");
	blocked("gh pr merge 1 -R github.com/trycua/cua");
	blocked("gh pr merge 1 --repo=https://github.com/trycua/cua.git");
	blocked("gh run rerun 5 -R github.com/TryCua/cua");
	blocked("gh api repos/trycua/cua/pulls/1/merge -f x=y -X PUT");
	allowed("cd /zs/wt && gh pr merge 33 -R injaneity/zstack", inCua);
	allowed("gh api repos/trycua/cua/pulls/1/merge -f x=y -X GET");
	allowed("gh api repos/trycua/cua/actions/runs/9/rerun");
	allowed("gh pr merge 33 -R injaneity/zstack", inCua);
	allowed("gh pr merge 1", "/scratch");
	// an inherited GH_REPO names the repo in any directory, a checkout or not
	assert.match(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "trycua/cua" }, "/scratch") ?? "", /^blocked: /);
	assert.match(checkBash("gh run rerun 9 --failed", OWNED, { ...host, ghRepo: "trycua/cua" }, "/zs") ?? "", /^blocked: /);
	assert.equal(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "injaneity/zstack" }, "/scratch"), undefined);
	// clearing the inherited GH_REPO sends gh back to the checkout
	const zsEnv = { ...host, ghRepo: "injaneity/zstack" };
	assert.equal(checkBash("gh pr merge 1", OWNED, zsEnv, inCua), undefined);
	for (const c of ["GH_REPO= gh pr merge 1", "env -u GH_REPO gh pr merge 1", "env --unset=GH_REPO gh pr merge 1", "env -i gh pr merge 1"])
		assert.match(checkBash(c, OWNED, zsEnv, inCua) ?? "", /^blocked: /, c);
	allowed("gh pr merge --help", inCua);
	allowed("gh pr merge 6474 -R trycua/cua --disable-auto");
	assert.equal(checkBash("gh pr merge 7031 -R trycua/cua", mergeOwned(""), host, inCua), undefined);
	assert.deepEqual([...mergeOwned(" trycua/cua , A/B ")], ["trycua/cua", "a/b"]);
});

test("readying a draft in a merge-job repo is blocked, through gh or GraphQL", () => {
	blocked("gh pr ready 7040 -R trycua/cua");
	blocked("gh pr ready 7040", inCua);
	blocked("gh pr ready https://github.com/trycua/cua/pull/7040");
	blocked(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -F query=@m.graphql -f q='enablePullRequestAutoMerge'`);
	allowed("gh pr ready 7040 -R trycua/cua --undo");
	allowed("gh pr ready 33 -R injaneity/zstack", inCua);
	allowed(`gh api graphql -f query='{ repository(owner:"trycua",name:"cua"){ pullRequest(number:1){ isDraft } } }'`);
	allowed("gh pr create --draft --title x --body y", inCua);
	allowed("gh pr view 7040 --json isDraft", inCua);
});

test("here-document bodies are data unless a shell reads them", () => {
	allowed("cd /zs && python3 - <<'EOF'\nsub('docs', 'never gh pr merge 1 or git push -f')\nEOF");
	allowed("cat > /tmp/body.md <<EOF\nwe never run gh pr merge here\nEOF\ngh pr create --body-file /tmp/body.md", inCua);
	blocked("python3 - <<'EOF'\nx\nEOF\ngit push -f");
	blocked("bash <<'EOF'\ngit push --force\nEOF");
	blocked("~/onigiri-vm/vm <<'EOF'\necho x >> /srv/onigiri/hold-prs.txt\nEOF");
	blocked("cat <<EOF\nx"+"\ngit push -f"); // no closing line: nothing is stripped
	blocked("echo '<<EOF'\ngit push -f\nEOF"); // a quoted << is text, so the next line runs
	blocked("cat <<EOF\n$(git push -f)\nEOF"); // an unquoted body's substitutions run
	blocked("echo hi # <<EOF\ngit push --force\nEOF"); // a << in a comment is text
	blocked("cat <<'EOF' | bash\ngit push -f\nEOF"); // piped into a shell, the body runs
	blocked("cat <<EOF.txt\ndata\nEOF.txt\ngit push -f\nEOF"); // the delimiter is the whole word
	blocked("cat <<'EOF' | sh -s\ngh pr merge 1 -R trycua/cua\nEOF");
	allowed("cat <<'EOF'\n$(git push -f)\nEOF"); // a quoted delimiter keeps it text
});

test("writes to the holds file are blocked; reads run", () => {
	for (const c of [
		"echo 'hold | 1 | no | x' >> /srv/onigiri/hold-prs.txt",
		"printf x > hold-prs.txt",
		"sed -i '/7031/d' /srv/onigiri/hold-prs.txt",
		"sed -i.bak 's/a/b/' hold-prs.txt",
		"cp /tmp/new /srv/onigiri/hold-prs.txt",
		"mv hold-prs.txt.new hold-prs.txt",
		"echo x | tee -a /srv/onigiri/hold-prs.txt",
		"rm /srv/onigiri/hold-prs.txt",
		"chmod u+w ~/onigiri/sync/hold-prs.txt",
		"~/onigiri-vm/vm \"echo x >> /srv/onigiri/hold-prs.txt\"",
		"cp /tmp/new hold-prs.txt --verbose",
		"cp -f /tmp/new /srv/onigiri/hold-prs.txt",
		"perl -pi -e 's/a/b/' hold-prs.txt",
		"echo x 2>>hold-prs.txt",
		"dd if=/tmp/x of=/srv/onigiri/hold-prs.txt",
		"echo x>hold-prs.txt",
		"echo x>>/srv/onigiri/hold-prs.txt",
		"cp -t /srv/onigiri /tmp/hold-prs.txt",
		"cp /tmp/hold-prs.txt /srv/onigiri/",
		"cp -p /tmp/hold-prs.txt $ROOT",
		"ssh host 'cp /tmp/hold-prs.txt /srv/onigiri'",
		"cp /tmp/hold-prs.txt /srv/$D",
		"cp /tmp/hold-prs.txt /srv/onig*",
		"mv /tmp/hold-prs.txt /srv/onigiri",
		"cp /tmp/hold-prs.txt sync",
		"mv /srv/onigiri/hold-prs.txt /tmp/holds-copy",
		"rsync --remove-source-files hold-prs.txt /tmp/x",
	])
		blocked(c);
	for (const c of [
		"cat /srv/onigiri/hold-prs.txt",
		"grep 7031 /srv/onigiri/hold-prs.txt",
		"cp /srv/onigiri/hold-prs.txt /tmp/holds-copy",
		"cp /srv/onigiri/hold-prs.txt /srv/onigiri/hold-prs.txt.bak-x",
		"diff hold-prs.txt hold-prs.txt.bak-1105",
		"~/onigiri-vm/vm 'cat /srv/onigiri/hold-prs.txt' > ~/onigiri/sync/hold-prs.txt.new",
		'git commit -m "fix: use rm hold-prs.txt in docs"',
		"tee hold-prs.txt.bak < hold-prs.txt",
		"sed -n 1,5p hold-prs.txt",
		"cp hold-prs.txt /tmp/x.txt",
		'echo "x >hold-prs.txt"',
		"cp /srv/onigiri/hold-prs.txt ~/scratch/holds.bak-1108",
		"cp ~/onigiri/sync/hold-prs.txt dry/root/", // a fixture copy into a directory with no holds file
		"cp /srv/onigiri/hold-prs.txt /tmp",
	])
		allowed(c);
	// the first cd may succeed, so the session's own directory still counts
	assert.match(checkBash("cd /srv/onigiri || cd /tmp; cp /tmp/hold-prs.txt .", OWNED, host, "/zs") ?? "", /^blocked: /);
	assert.match(checkBash("cd /tmp || true; cp /tmp/hold-prs.txt .", OWNED, host, "/srv/onigiri") ?? "", /^blocked: /);
	// a relative cd resolves from every directory the line might be in: here the first cd fails, so `sync` is /zs/sync
	assert.match(checkBash("cd /tmp/x extra || cd sync; cp /tmp/hold-prs.txt .", OWNED, host, "/zs") ?? "", /^blocked: /);
	assert.match(checkBash("cd /tmp/x extra || cd sync; gh pr merge 1", OWNED, host, "/cua") ?? "", /^blocked: /);
	assert.match(checkWrite("/srv/onigiri/hold-prs.txt") ?? "", /^blocked: /);
	assert.equal(checkWrite("/srv/onigiri/policy.md"), undefined);
	assert.equal(checkWrite("hold-prs.txt.md"), undefined);
});

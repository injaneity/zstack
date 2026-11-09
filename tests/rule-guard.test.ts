// Tests for extensions/rule-guard/rules.ts. Run: node --test tests/rule-guard.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkBash, checkWrite, mergeOwned } from "../extensions/rule-guard/rules.ts";

const OWNED = mergeOwned("trycua/cua");
// a fake `git remote get-url origin`: any directory under /cua is a trycua/cua checkout
const repoAt = (dir: string) => (dir.startsWith("/cua") ? "trycua/cua" : dir.startsWith("/zs") ? "injaneity/zstack" : undefined);
// and a fake filesystem whose only directories are these
const DIRS = new Set(["/srv/onigiri", "/zs/sync"]);
const host = { repoAt, isDir: (p: string) => DIRS.has(p.replace(/\/$/, "")) };
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
	assert.equal(checkBash("gh pr merge 7031 -R trycua/cua", mergeOwned(""), host, inCua), undefined);
	assert.deepEqual([...mergeOwned(" trycua/cua , A/B ")], ["trycua/cua", "a/b"]);
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
	])
		allowed(c);
	assert.match(checkWrite("/srv/onigiri/hold-prs.txt") ?? "", /^blocked: /);
	assert.equal(checkWrite("/srv/onigiri/policy.md"), undefined);
	assert.equal(checkWrite("hold-prs.txt.md"), undefined);
});

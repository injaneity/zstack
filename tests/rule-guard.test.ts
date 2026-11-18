import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { holdsPaths, isHoldsPath } from "../extensions/rule-guard/holds.ts";
import { execFileSync } from "node:child_process";
import { checkBash, checkWrite, mergeOwned, prTextProblems, readyAllowed, reviewOnlyLogins } from "../extensions/rule-guard/rules.ts";

for (const k of ["ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"]) delete process.env[k];

const DUMP = () => execFileSync("python3", ["-I", new URL("../bin/policy", import.meta.url).pathname, "dump"], { encoding: "utf8" });
const OWNED = mergeOwned(DUMP);
const ISSUES = OWNED;
const repoAt = (dir: string) => (dir.startsWith("/app") ? "acme/app" : dir.startsWith("/zs") ? "injaneity/zstack" : dir.startsWith("/scratch") ? null : undefined);
const HOLDS = new Set(["/srv/onigiri/hold-prs.txt", "/zs/sync/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"]);
const TEAM = reviewOnlyLogins(["review-only    | f-teammate      | no  | teammate\nreview-only | ddupont808 | no | teammate\n# review-only | retired | no | x\nhold | 5675 | yes | x\n"]);
const AUTHORS: Record<number, string> = { 7030: "f-teammate", 4468: "DDupont808", 3138: "octo", 6853: "someone" };
const host = { repoAt, isHolds: (p: string) => HOLDS.has(p.startsWith("~") ? p : path.posix.resolve(p)), reviewOnly: () => TEAM,
	authorOf: (_repo: string, n: number) => AUTHORS[n] };
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
	blocked("if true; then echo x >> hold-prs.txt; fi", "/srv/onigiri");
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

test("a brief that allows merging lets the session ready its draft, and nothing else", () => {
	const ok = (c: string, cwd = "/zs") => assert.equal(checkBash(c, OWNED, host, cwd, true), undefined, c);
	const no = (c: string, cwd = "/zs") => assert.match(checkBash(c, OWNED, host, cwd, true) ?? "", /^blocked: /, c);
	ok("gh pr ready 7040 -R acme/app");
	ok("gh pr ready 7040", inApp);
	ok(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no("gh pr merge 7040 -R acme/app --squash");
	no("gh pr merge 7040 --auto", inApp);
	ok("gh run rerun 9 -R acme/app");
	no("gh api -X PUT repos/acme/app/pulls/7040/merge");
	no(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no("git push -f origin HEAD:main");
	assert.equal(readyAllowed("1"), true);
	for (const v of ["", "0", "true", "yes", " 1"]) assert.equal(readyAllowed(v), false, v);
	blocked("ONIGIRI_READY_ALLOWED=1 gh pr ready 7040 -R acme/app");
	blocked("env ONIGIRI_READY_ALLOWED=1 gh pr ready 7040", inApp);
	blocked("ONIGIRI_READY_ALLOWED=1 pi -p 'gh pr ready 7040 -R acme/app'");
	blocked("export ONIGIRI_READY_ALLOWED=1; pi -p go");
	blocked("ONIGIRI_READY_ALLOWED=1; export ONIGIRI_READY_ALLOWED");
	blocked("bash -c 'env ONIGIRI_READY_ALLOWED=1 pi -p go'");
	blocked("printf -v ONIGIRI_READY_ALLOWED %s 1; pi -p go");
	blocked("read ONIGIRI_READY_ALLOWED <<< 1; pi -p go");
	blocked("declare -x ONIGIRI_READY_ALLOWED=1; pi -p go");
	blocked(`export ONIGIRI_READY_""ALLOWED=1; pi -p go`);
	blocked("export ONIGIRI_READY_'A'LLOWED=1; pi -p go");
	blocked("export ONIGIRI_READY_\\ALLOWED=1; pi -p go");
	blocked("ONIGIRI_READY_ALLOWED1=1 true");
	blocked("python3 - <<'EOF'\nimport os; os.environ['ONIGIRI_READY_ALLOWED'] = '1'; os.execvp('pi', ['pi', '-p', 'go'])\nEOF");
	blocked(`set -a; : "\${ONIGIRI_READY_ALLOWED:=1}"; pi -p go`);
	blocked("echo ${ONIGIRI_READY_ALLOWED=1}");
	blocked("export $'ONIGIRI_READY_ALLOWED'=1; pi -p go");
	blocked(`export $"ONIGIRI_READY_ALLOWED"=1; pi -p go`);
	blocked("export \\$ONIGIRI_READY_ALLOWED=1");
	blocked("echo ${ONIGIRI_READY_ALLOWED:-unset}");
	allowed(`echo $ONIGIRI_READY_ALLOWED "[$ONIGIRI_READY_ALLOWED]"`);
});

test("here-document bodies are data unless a shell reads them", () => {
	allowed("cd /zs && python3 - <<'EOF'\nsub('docs', 'never gh pr merge 1 or git push -f')\nEOF");
	allowed("cat > /tmp/body.md <<EOF\nwe never run gh pr merge here\nEOF\ngh pr create --body-file /tmp/body.md", inApp);
	blocked("python3 - <<'EOF'\nx\nEOF\ngit push -f");
	blocked("bash <<'EOF'\ngit push --force\nEOF");
	blocked("~/onigiri-vm/vm <<'EOF'\necho x >> /srv/onigiri/hold-prs.txt\nEOF");
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

test("writes to the real holds file are blocked; reads run", () => {
	const root = "/srv/onigiri";
	for (const c of [
		"echo 'hold | 1 | no | x' >> /srv/onigiri/hold-prs.txt",
		"printf x > hold-prs.txt",
		"sed -i '/7031/d' /srv/onigiri/hold-prs.txt",
		"sed -i.bak 's/a/b/' hold-prs.txt",
		"cp /tmp/new /srv/onigiri/hold-prs.txt",
		"mv hold-prs.txt.new hold-prs.txt",
		"echo x | tee -a /srv/onigiri/hold-prs.txt",
		"rm /srv/onigiri/hold-prs.txt",
		"rm ./hold-prs.txt",
		"rm ../onigiri/hold-prs.txt",
		"chmod u+w ~/onigiri/sync/hold-prs.txt",
		"~/onigiri-vm/vm \"echo x >> /srv/onigiri/hold-prs.txt\"",
		"cp /tmp/new hold-prs.txt --verbose",
		"cp -f /tmp/new /srv/onigiri/hold-prs.txt",
		"perl -pi -e 's/a/b/' hold-prs.txt",
		"echo x 2>>hold-prs.txt",
		"dd if=/tmp/x of=/srv/onigiri/hold-prs.txt",
		"echo x>hold-prs.txt",
		"echo x>>/srv/onigiri/hold-prs.txt",
		"echo x > /zs/sync/hold-prs.txt",
		"cp -t /srv/onigiri /tmp/hold-prs.txt",
		"cp --target-directory=/srv/onigiri /tmp/hold-prs.txt",
		"cp /tmp/hold-prs.txt /srv/onigiri/",
		"cp -p /tmp/hold-prs.txt $ROOT",
		"cp /tmp/hold-prs.txt /srv/$D",
		"cp /tmp/hold-prs.txt /srv/onig*",
		"mv /tmp/hold-prs.txt /srv/onigiri",
		"cp /tmp/hold-prs.txt /zs/sync",
		"mv /srv/onigiri/hold-prs.txt /tmp/holds-copy",
		"mv hold-prs.txt /tmp/holds-copy",
		"mv -t /tmp /srv/onigiri/hold-prs.txt",
		"mv --target-directory=/tmp hold-prs.txt",
		"rsync --remove-source-files -t /tmp hold-prs.txt",
		"cp /tmp/x/hold-prs.txt /srv/onigiri/",
		"cp /tmp/x/hold-prs.txt /srv/onigiri//",
		"rsync --remove-source-files hold-prs.txt /tmp/x",
		"rm $ROOT/hold-prs.txt",
		"rm ~other/hold-prs.txt",
		"rm /srv/{onigiri,x}/hold-prs.txt",
		...(process.platform === "darwin" ? ["rm $ROOT/HOLD-PRS.TXT"] : []),
	])
		blocked(c, root);
	blocked("ssh host 'cp /tmp/hold-prs.txt /srv/onigiri'");
	blocked("ssh host 'echo x > /tmp/t/hold-prs.txt'");
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
		"cp ~/onigiri/sync/hold-prs.txt /zs/dry/root/",
		"cp /srv/onigiri/hold-prs.txt /tmp",
	])
		allowed(c, root);
	for (const c of [
		"printf 'hold | 1 | all | x\\n' > /tmp/t/vm/hold-prs.txt",
		"echo x >> hold-prs.txt",
		"rm -f hold-prs.txt",
		"mv hold-prs.txt.new hold-prs.txt",
		"sed -i '' 1d /tmp/t/hold-prs.txt",
		"cp /srv/onigiri/hold-prs.txt .",
		"cp /tmp/hold-prs.txt /zs/dry/root/",
		"cd /tmp/home && touch hold-prs.txt",
		"cd -P /tmp/home && touch hold-prs.txt",
		"pushd /tmp/home && touch hold-prs.txt",
		"env -i rm hold-prs.txt",
		"HOME=/tmp/h bash -c 'rm /tmp/h/onigiri/hold-prs.txt'",
		"bash -c 'rm hold-prs.txt'",
		"sh -c 'cd /tmp/t && echo x > hold-prs.txt'",
	])
		allowed(c, "/zs/wt");
	assert.match(checkBash("cd /srv/onigiri || cd /tmp; cp /tmp/hold-prs.txt .", OWNED, host, "/zs") ?? "", /^blocked: /);
	assert.match(checkBash("bash -c 'rm hold-prs.txt'", OWNED, host, "/srv/onigiri") ?? "", /^blocked: /);
	assert.match(checkBash("cd /srv/onigiri && bash -c 'echo x > hold-prs.txt'", OWNED, host, "/tmp") ?? "", /^blocked: /);
	assert.match(checkBash("cd /tmp || true; cp /tmp/hold-prs.txt .", OWNED, host, "/srv/onigiri") ?? "", /^blocked: /);
	assert.match(checkBash("cd /tmp/x extra || cd sync; cp /tmp/hold-prs.txt .", OWNED, host, "/zs") ?? "", /^blocked: /);
	assert.match(checkBash("cd /tmp/x extra || cd sync; gh pr merge 1", OWNED, host, "/app") ?? "", /^blocked: /);
	for (const c of [
		"cd /tmp/$TARGET && rm hold-prs.txt",
		"cd /srv/onig?ri && rm hold-prs.txt",
		"cd /srv/{onigiri,x} && rm hold-prs.txt",
		"cd /tmp/`printf x` && rm hold-prs.txt",
		"cd /tmp/$(printf x) && rm hold-prs.txt",
		'cd "/tmp/$(printf x)" && rm hold-prs.txt',
		"cd $(printf /tmp)/x; rm hold-prs.txt",
		"cd /tmp; cd /srv/x; cd -; rm hold-prs.txt",
		"pushd /tmp && popd && rm hold-prs.txt",
		"env -C /srv/onigiri rm hold-prs.txt",
		"env --chdir=/srv/onigiri rm hold-prs.txt",
		"sudo -D /srv/onigiri rm hold-prs.txt",
		"env -C /srv/onigiri bash -c 'rm hold-prs.txt'",
		"ssh host 'rm hold-prs.txt'",
		"cd ~/x && rm hold-prs.txt",
		"CDPATH=/srv; cd onigiri && rm hold-prs.txt",
		"export CD''PATH=/srv && cd onigiri && rm hold-prs.txt",
		"cd onigiri && rm hold-prs.txt",
		"cd ./x && rm hold-prs.txt",
	])
		assert.match(checkBash(c, OWNED, host, "/tmp") ?? "", /^blocked: /, c);
	for (const c of ["cd -P /srv/onigiri && rm hold-prs.txt", "pushd /srv/onigiri && rm hold-prs.txt", "cd -L /zs/sync; echo x > hold-prs.txt"])
		assert.match(checkBash(c, OWNED, host, "/tmp") ?? "", /^blocked: /, c);
	const write = (f: string, cwd?: string) => checkWrite(f, host, cwd) ?? "";
	assert.match(write("/srv/onigiri/hold-prs.txt"), /^blocked: /);
	assert.match(write("@/srv/onigiri/hold-prs.txt"), /^blocked: /);
	assert.match(write("hold-prs.txt", "/srv/onigiri"), /^blocked: /);
	assert.match(write("sync/hold-prs.txt", "/zs"), /^blocked: /);
	assert.match(write("hold-prs.txt"), /^blocked: /);
	assert.equal(write("/srv/onigiri/notes.md"), "");
	assert.equal(write("hold-prs.txt.md"), "");
	assert.equal(write("/tmp/t/hold-prs.txt"), "");
	assert.equal(write("hold-prs.txt", "/tmp/t"), "");
});

test("bin/hold-row is the sanctioned way to append a row relaying Robin's call; direct writes still name it", () => {
	const row = "preapprove | #7338 | no | " + "a".repeat(40) + " | deep; Robin 11 Nov 14:56 SGT (green at pin)";
	for (const c of [
		`bin/hold-row add '${row}'`,
		`~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row add "${row}"`,
		`ONIGIRI_ROOT=/srv/onigiri bin/hold-row add '${row}'`,
		`~/onigiri-vm/vm "~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row add '${row}'"`,
		`ssh vm 'bin/hold-row add "hold | 7400 | no | Robin 11 Nov 17:00 SGT: wait for bench"'`,
		`bin/hold-row retire '${row}' 'Robin 11 Nov 18:00 SGT: stale pin'`,
		`~/onigiri-vm/vm "~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row replace '${row}' '${row.replace("a".repeat(40), "b".repeat(40))}'"`,
	])
		allowed(c, "/srv/onigiri");
	blocked(`bin/hold-row add '${row}' > /srv/onigiri/hold-prs.txt`, "/srv/onigiri");
	blocked(`bin/hold-row add '${row}'; echo x >> /srv/onigiri/hold-prs.txt`, "/srv/onigiri");
	assert.match(checkBash("echo x >> /srv/onigiri/hold-prs.txt", OWNED, host) ?? "", /bin\/hold-row add/);
	assert.match(checkWrite("/srv/onigiri/hold-prs.txt", host) ?? "", /bin\/hold-row add/);
});

test("the real holds paths are this host's config, matched on the filesystem with symlinks followed", () => {
	assert.deepEqual(holdsPaths({ ONIGIRI_ROOT: "/srv/onigiri" }, "~/copy/hold-prs.txt"), ["/srv/onigiri/hold-prs.txt", "~/copy/hold-prs.txt"]);
	assert.deepEqual(holdsPaths({}), ["~/zstack/hold-prs.txt"]);
	assert.deepEqual(holdsPaths({}, "~/zstack/hold-prs.txt"), ["~/zstack/hold-prs.txt"]);
	const t = fs.mkdtempSync(path.join(os.tmpdir(), "rule-guard-"));
	try {
		const root = path.join(t, "root"), other = path.join(t, "test-home", "onigiri");
		fs.mkdirSync(root);
		fs.mkdirSync(other, { recursive: true });
		fs.writeFileSync(path.join(root, "hold-prs.txt"), "hold | 1 | all | x\n");
		fs.symlinkSync(root, path.join(t, "link"));
		fs.symlinkSync(path.join(root, "hold-prs.txt"), path.join(t, "file-link"));
		fs.linkSync(path.join(root, "hold-prs.txt"), path.join(t, "hard-link"));
		const holds = holdsPaths({ ONIGIRI_ROOT: root });
		const real = { repoAt: () => undefined, isHolds: (p: string) => isHoldsPath(p, holds, t) };
		const no = (c: string, cwd = t) => assert.match(checkBash(c, OWNED, real, cwd) ?? "", /^blocked: /, c);
		const ok = (c: string, cwd = t) => assert.equal(checkBash(c, OWNED, real, cwd), undefined, c);
		for (const c of [`echo x >> ${root}/hold-prs.txt`, `rm ${root}/hold-prs.txt`, `mv ${root}/hold-prs.txt /tmp/x`, `cp /tmp/x ${root}/hold-prs.txt`, `sed -i '' 1d ${root}/hold-prs.txt`])
			no(c);
		no("echo x > link/hold-prs.txt");
		no("rm link/hold-prs.txt");
		no("echo x > file-link");
		no("rm hard-link");
		fs.mkdirSync(path.join(root, "sub"));
		fs.symlinkSync(path.join(root, "sub"), path.join(t, "sublink"));
		no("echo x > sublink/../hold-prs.txt");
		no("cd sublink/.. && rm hold-prs.txt");
		fs.symlinkSync(other, path.join(root, "away"));
		no(`cd ${root}/away/.. && rm hold-prs.txt`, other);
		no(`cd -L ${root}/away/.. && rm hold-prs.txt`, other);
		no(`mv -t /tmp ${t}/sublink/../hold-prs.txt`);
		no("cp /tmp/x/hold-prs.txt sublink/..");
		assert.match(checkWrite("sublink/../hold-prs.txt", real, t) ?? "", /^blocked: /);
		ok("echo x > sublink/hold-prs.txt");
		no("echo x > hold-prs.txt", root);
		no("echo x > ../../root/./hold-prs.txt", other);
		no("echo x > ~/root/hold-prs.txt");
		assert.match(checkWrite(path.join(t, "link", "hold-prs.txt"), real) ?? "", /^blocked: /);
		fs.renameSync(path.join(root, "hold-prs.txt"), path.join(root, "gone"));
		no("echo x > link/hold-prs.txt");
		fs.symlinkSync(path.join(root, "hold-prs.txt"), path.join(t, "alias"));
		fs.symlinkSync("../../link/hold-prs.txt", path.join(other, "rel-alias"));
		no("echo x > alias");
		no("echo x > rel-alias", other);
		assert.match(checkWrite(path.join(t, "alias"), real) ?? "", /^blocked: /);
		if (process.platform === "darwin") no(`echo x > ${root.toUpperCase()}/HOLD-PRS.TXT`);
		fs.symlinkSync(path.join(t, "loop"), path.join(t, "loop"));
		ok("touch loop");
		no("rm loop/hold-prs.txt");
		ok("rm loop/notes.md");
		for (const c of [`printf 'hold | 2 | all | y\\n' > ${other}/hold-prs.txt`, `echo x >> ${other}/hold-prs.txt`, `mv ${other}/hold-prs.txt ${other}/old`, `rm ${other}/hold-prs.txt`])
			ok(c);
		ok("touch hold-prs.txt && rm hold-prs.txt", other);
		assert.equal(checkWrite(path.join(other, "hold-prs.txt"), real), undefined);
		assert.equal(checkWrite("hold-prs.txt", real, other), undefined);
	} finally {
		fs.rmSync(t, { recursive: true, force: true });
	}
});

test("the owned repos are every repo the config has, and an unreadable config refuses every merge and ready", () => {
	assert.deepEqual(OWNED, new Set(["acme/app"]));
	assert.deepEqual(mergeOwned(() => '{"repos": ["Acme/App", "acme/tool"]}'), new Set(["acme/app", "acme/tool"]));
	for (const dump of [() => { throw new Error("policy: missing merge.repo"); }, () => "not json", () => "{}", () => '{"repos": ["app"]}', () => '{"repos": []}', () => '{"merge": {"repo": "acme/app"}}'])
		assert.equal(mergeOwned(dump), null);
	for (const c of ["gh pr merge 33 -R injaneity/zstack --squash", "gh pr ready 1", "gh api -X PUT repos/x/y/pulls/1/merge",
		"gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'"])
		assert.match(checkBash(c, null, host, "/zs") ?? "", /bin\/policy dump failed/, c);
	for (const c of ["gh pr view 1", "gh run rerun 9 --failed", "gh pr merge 1 --help", "git push"])
		assert.equal(checkBash(c, null, host, "/zs"), undefined, c);
});

test("closing a acme/app issue by hand needs a comment naming the PR that fixed it", () => {
	const no = (c: string, cwd = "/zs", h = host) => assert.match(checkBash(c, OWNED, h, cwd, false, ISSUES) ?? "", /^blocked: .*issue-close/, c);
	const ok = (c: string, cwd = "/zs", h = host) => assert.equal(checkBash(c, OWNED, h, cwd, false, ISSUES), undefined, c);
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
	assert.match(checkBash("gh issue close 12 -R injaneity/zstack", OWNED, host, "/zs", false, null) ?? "", /^blocked: /);
	assert.equal(checkBash("gh issue close 6853 -R acme/app", OWNED, host, "/zs", false, new Set()), undefined);
});

test("a PR or issue a review-only teammate opened is never closed, by the author gh reports", () => {
	const no = (c: string, h = host) => assert.match(checkBash(c, OWNED, h, "/zs", false, ISSUES) ?? "", /^blocked: .*, a review-only teammate\. /, c);
	const ok = (c: string, h = host) => assert.equal(checkBash(c, OWNED, h, "/zs", false, ISSUES), undefined, c);
	assert.deepEqual(TEAM, new Set(["f-teammate", "ddupont808"]));
	for (const c of [
		'gh issue close 7030 -R acme/app --comment "Fixed by #7147."',
		'gh pr close 7030 -R acme/app --comment "Replaced by #7147, which merged."',
		'gh pr close https://github.com/acme/app/pull/4468 -c "Replaced by #4560"',
		'gh issue close https://github.com/acme/app/issues/4468 --comment "Fixed by #4560"',
		"gh api -X PATCH repos/acme/app/pulls/7030 -f state=closed",
		'ssh vm "gh pr close 7030 -R acme/app"',
	]) no(c);
	for (const c of ["gh api -X PATCH repos/{owner}/{repo}/pulls/7030 -f state=closed", "gh pr close 7030 -c 'Replaced by #7147'"])
		assert.match(checkBash(c, OWNED, host, inApp, false, ISSUES) ?? "", /, a review-only teammate\. /, c);
	ok('gh pr close 3138 -R acme/app --comment "Replaced by #7587, which merged."');
	ok('gh issue close 6853 -R acme/app --comment "Fixed by #6928."');
	ok("gh pr close 7030 -R injaneity/zstack");
	ok("gh pr view 7030 -R acme/app");
	const refuse = (c: string, h: typeof host, why: RegExp) => assert.match(checkBash(c, OWNED, h, "/zs", false, ISSUES) ?? "", why, c);
	refuse('gh pr close 3138 -R acme/app -c "Replaced by #7587"', { ...host, reviewOnly: () => null }, /no hold-prs.txt could be read/);
	refuse('gh pr close 9999 -R acme/app -c "Replaced by #7587"', host, /could not tell/);
	refuse('gh pr close -R acme/app -c "Replaced by #7587"', host, /without a number/);
	refuse(`gh api graphql -f query='mutation { closePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`, host, /GraphQL close/);
	for (const c of ["gh api graphql -F query=@close.graphql", "gh api graphql --field=query=@q.graphql -F id=X"]) refuse(c, host, /GraphQL query read from a file/);
	for (const c of ["gh api graphql --input mutation.json", "gh api graphql --input=- < m.json", "gh api graphql -Fquery=@q"])
		refuse(c, host, /GraphQL query read from a file or stdin/);
	for (const c of ["gh api -X PATCH repos/acme/app/pulls/7030 -F state=@state.txt", "gh api -X PATCH repos/acme/app/pulls/7030 --field=state=@s",
		"gh api -X PATCH repos/acme/app/pulls/7030 -fstate=closed", "gh api -X PATCH repos/acme/app/pulls/7030 --raw-field=state=closed",
		"gh api -X PATCH repos/acme/app/pulls/7030 -F state=CLOSED", "gh api -X PATCH repos/acme/app/pulls/7030 --raw-field state=closed",
		"gh api -X PATCH repos/acme/app/pulls/7030 -Fstate=@-"])
		refuse(c, host, /, a review-only teammate\. /);
	ok("gh api graphql -f query='query { viewer { login } }'");
	ok("gh api -X PATCH repos/acme/app/pulls/7030 -F title=@t.txt");
	ok("gh api -X PATCH repos/acme/app/pulls/7030 -f state=open");
	assert.equal(reviewOnlyLogins([undefined, undefined]), null);
	assert.deepEqual(reviewOnlyLogins([undefined, ""]), new Set());
});

test("a PR title or body that breaks Commits and PR text is refused in the merge job's repo, and nowhere else", () => {
	const files: Record<string, string> = { "/app/wt/body.md": "## Summary\nx\n\nverify: ran\n", "/app/wt/ok.md": "## Summary\nthe scoped repository checks passed\nrisk: light\n" };
	const h = { ...host, readText: (f: string, d: string | undefined) => files[path.posix.resolve(d ?? "/", f)] };
	const no = (c: string, why: RegExp, cwd = inApp) => assert.match(checkBash(c, OWNED, h, cwd) ?? "", why, c);
	const ok = (c: string, cwd = inApp) => assert.equal(checkBash(c, OWNED, h, cwd), undefined, c);
	no('gh pr create --title "Fix the driver" --body "x"', /^blocked: a PR title that is not Conventional Commit/);
	no('gh pr create -R acme/app -t "fix: x" -b "built in /home/box/work/app"', /local machine path/, "/zs");
	no('gh pr edit 12 --body "x\nverify-ran: clippy"', /`verify:` or `verify-ran:` line/);
	no('gh pr create --title "fix(driver): x" --body "x\n\nCo-authored-by: A <a@b.c>"', /Co-authored-by/);
	no("gh pr create --title 'fix(driver): x' --body-file body.md", /`verify:`/);
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
		`gh alias set m 'pr merge'`, `gh m 40 --auto -R acme/app`, `gh alias import aliases.yml`])
		assert.match(run(c) ?? "", /^blocked: /, c);
	for (const c of ["gh alias list", "gh pr view 40", "gh pr -R acme/app view 40", "gh run rerun 9 --failed", "gh co 40", "gh m --help"])
		assert.equal(run(c), undefined, c);
	assert.equal(run(`gh pr merge 40 -s -d --match-head-commit ${H}`), undefined);
	asked.pop();
	assert.equal(asked.length, 3);
	assert.match(checkBash(`gh pr merge 40 --squash --match-head-commit ${H}`, OWNED, host, "/app") ?? "", /^blocked: /);
});

test("a coding thread reports to the queue but never moves it, and never marks a head verified", () => {
	const verdict = "ZSTACK_CONFIG=/c ZSTACK_REPO=acme/app python3 /pkg/zstack/bin/queue verified app-1 40 abc pass auto";
	const inThread = { ...host, thread: "t-9" };
	for (const c of ["bin/queue start app-1", "/pkg/zstack/bin/queue claim app-1 t-9", verdict, "/pkg/zstack/bin/queue requeue app-1", "bin/queue release app-2",
		"queue add x", "cd /tmp && /pkg/zstack/bin/queue sweep", "bin/queue requeue app-1", "bin/queue pickup",
		"gh api -X POST repos/acme/app/statuses/abc -f state=success -f context=zstack/verified",
		"gh api repos/acme/app/check-runs -f name=zstack/verified -f head_sha=abc"])
		blocked(c, inApp);
	for (const c of ["/pkg/zstack/bin/queue release", "bin/queue gate 40 abc", "bin/queue list", "gh api repos/acme/app/statuses/abc",
		"gh api repos/acme/app/commits/abc/check-runs", "ls bin/queue", "git add bin/queue"])
		allowed(c, inApp);
	for (const c of ["bin/queue start app-1", "bin/queue verified app-1 40 abc pass auto",
		"gh api -X POST repos/acme/app/statuses/abc -f state=success -f context=zstack/verified"])
		allowed(c, "/zs");
	assert.match(checkBash("bin/queue start app-1", OWNED, host, inApp) ?? "", /only the dispatcher moves the queue/);
	assert.equal(checkBash(verdict, OWNED, inThread, inApp), undefined);
	assert.equal(checkBash("echo $T3CODE_THREAD_ID", OWNED, inThread, inApp), undefined);
	for (const c of [`T3CODE_THREAD_ID=t-0 ${verdict}`, `env -u T3CODE_THREAD_ID ${verdict}`, `unset T3CODE_THREAD_ID; ${verdict}`,
		`export T3CODE_THREAD_ID=t-0 && ${verdict}`, "bin/queue start app-1"])
		assert.match(checkBash(c, OWNED, inThread, inApp) ?? "", /^blocked: /, c);
});


import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";
import { holdsPaths, isHoldsPath } from "../extensions/rule-guard/holds.ts";
import { execFileSync } from "node:child_process";
import { checkBash, checkWrite, issuesOwned, mergeOwned, readyAllowed, reviewOnlyLogins } from "../extensions/rule-guard/rules.ts";

const DUMP = () => execFileSync("python3", ["-I", new URL("../bin/policy", import.meta.url).pathname, "dump"], { encoding: "utf8" });
const OWNED = mergeOwned(DUMP);
const ISSUES = issuesOwned(DUMP);
const repoAt = (dir: string) => (dir.startsWith("/cua") ? "trycua/cua" : dir.startsWith("/zs") ? "injaneity/zstack" : dir.startsWith("/scratch") ? null : undefined);
const HOLDS = new Set(["/srv/onigiri/hold-prs.txt", "/zs/sync/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"]);
const TEAM = reviewOnlyLogins(["review-only    | f-trycua      | no  | teammate\nreview-only | ddupont808 | no | teammate\n# review-only | retired | no | x\nhold | 5675 | yes | x\n"]);
const AUTHORS: Record<number, string> = { 7030: "f-trycua", 4468: "DDupont808", 3138: "injaneity", 6853: "someone" };
const host = { repoAt, isHolds: (p: string) => HOLDS.has(p.startsWith("~") ? p : path.posix.resolve(p)), reviewOnly: () => TEAM,
	authorOf: (_repo: string, n: number) => AUTHORS[n] };
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

test("merging in a merge-job repo is blocked", () => {
	blocked("gh pr merge 7031 -R trycua/cua --squash");
	blocked("gh pr merge 7031 --repo=trycua/cua --auto");
	blocked("gh pr merge https://github.com/trycua/cua/pull/7031");
	blocked("gh pr merge 7031 --squash", inCua);
	blocked("gh api -X PUT repos/trycua/cua/pulls/7031/merge");
	blocked("GH_CONFIG_DIR=x gh pr merge 1 -R TryCua/Cua");
	blocked("gh pr merge 1", "/tmp/x");
	blocked("cd /cua/wt && gh pr merge 1 --squash");
	blocked("cd ~/onigiri/cua && gh pr merge 1");
	blocked("gh api -X PUT repos/{owner}/{repo}/pulls/1/merge", inCua);
	blocked("gh api repos/trycua/cua/pulls/1/merge -f merge_method=squash");
});

test("approving a fork's workflow runs in a merge-job repo runs; a merge beside it stays blocked", () => {
	for (const c of ["gh api -X POST repos/trycua/cua/actions/runs/9/approve", "gh api --method POST repos/trycua/cua/actions/runs/$RUN/approve",
		"gh api -X POST repos/{owner}/{repo}/actions/runs/9/approve", "gh api -X POST /repos/trycua/cua/actions/runs/9/approve"]) {
		allowed(c);
		allowed(c, inCua);
		allowed(c, "/tmp/x");
	}
	blocked("gh api -X POST repos/trycua/cua/actions/runs/9/approve && gh pr merge 1 -R trycua/cua");
	blocked("gh api -X POST repos/trycua/cua/actions/runs/9/approve; gh api -X PUT repos/trycua/cua/pulls/1/merge", "/scratch");
});

test("CI reruns in a merge-job repo run, whatever the run's conclusion: the session decides them", () => {
	for (const c of ["gh run rerun 9 -R trycua/cua --failed", "gh run rerun 9 -R trycua/cua", "gh run rerun --job 90 -R trycua/cua",
		"gh run rerun 9 --job=90 -R trycua/cua", "gh run rerun $RUN -R trycua/cua", "gh run rerun 9 --failed",
		"gh api -X POST repos/trycua/cua/actions/runs/9/rerun-failed-jobs", "gh api -X POST repos/trycua/cua/actions/jobs/90/rerun",
		"gh api -X POST repos/{owner}/{repo}/actions/runs/9/rerun"]) {
		allowed(c);
		allowed(c, inCua);
		allowed(c, "/tmp/x");
	}
	assert.equal(checkBash("gh run rerun 9 --failed", OWNED, { ...host, ghRepo: "trycua/cua" }, "/scratch"), undefined);
	blocked("gh run rerun 9 -R trycua/cua && gh pr merge 1 -R trycua/cua");
	blocked("gh api -X PUT -f x=repos/trycua/cua/actions/runs/9/rerun repos/trycua/cua/pulls/1/merge", "/scratch");
	blocked("gh api -H x/repos/other/repo/pulls/1/merge -X PUT repos/trycua/cua/pulls/1/merge", "/scratch");
	blocked("gh pr merge 1 --squash -b https://github.com/other/repo/pull/1", inCua);
	const otherEnv = { ...host, ghRepo: "trycua/other" };
	for (const c of ["gh api -X PUT repos/{owner}/cua/pulls/1/merge", "gh api -X PUT repos/trycua/{repo}/pulls/1/merge"])
		assert.match(checkBash(c, OWNED, c.includes("{repo}") ? { ...host, ghRepo: "other/cua" } : otherEnv, "/scratch") ?? "", /^blocked: /, c);
	assert.equal(checkBash("gh api -X PUT repos/{owner}/zstack/pulls/1/merge", OWNED, otherEnv, "/scratch"), undefined);
	blocked("gh api -X PUT repos/{owner}/{bogus}/pulls/1/merge", inCua);
});

test("merges elsewhere run, and nothing is owned unless configured", () => {
	allowed("gh pr merge 33 -R injaneity/zstack --squash");
	allowed("gh pr merge 33 --squash");
	allowed("gh pr view 7031 -R trycua/cua --json state", inCua);
	allowed("gh pr checks 7031 -R trycua/cua", inCua);
	allowed("gh api repos/trycua/cua/pulls/123/merge");
	blocked("cd /zs/wt || gh pr merge 1", inCua);
	blocked("cd /zs/wt && gh pr merge 33", inCua);
	blocked("GH_REPO=trycua/cua gh pr merge 1");
	blocked("{ gh pr merge 1 -R trycua/cua; }");
	blocked("{ cd /cua; gh pr merge 1; }");
	blocked("if cd /cua; then gh pr merge 1; fi");
	blocked("if true; then echo x >> hold-prs.txt; fi", "/srv/onigiri");
	blocked("gh pr merge 1 -R github.com/trycua/cua");
	blocked("gh pr merge 1 --repo=https://github.com/trycua/cua.git");
	blocked("gh api repos/trycua/cua/pulls/1/merge -f x=y -X PUT");
	allowed("cd /zs/wt && gh pr merge 33 -R injaneity/zstack", inCua);
	allowed("gh api repos/trycua/cua/pulls/1/merge -f x=y -X GET");
	allowed("gh pr merge 33 -R injaneity/zstack", inCua);
	allowed("gh pr merge 1", "/scratch");
	assert.match(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "trycua/cua" }, "/scratch") ?? "", /^blocked: /);
	assert.equal(checkBash("gh pr merge 1", OWNED, { ...host, ghRepo: "injaneity/zstack" }, "/scratch"), undefined);
	const zsEnv = { ...host, ghRepo: "injaneity/zstack" };
	assert.equal(checkBash("gh pr merge 1", OWNED, zsEnv, inCua), undefined);
	for (const c of ["GH_REPO= gh pr merge 1", "env -u GH_REPO gh pr merge 1", "env --unset=GH_REPO gh pr merge 1", "env -i gh pr merge 1"])
		assert.match(checkBash(c, OWNED, zsEnv, inCua) ?? "", /^blocked: /, c);
	assert.match(checkBash("GH_REPO=trycua/cua bash -c 'gh pr merge 1'", OWNED, zsEnv, "/scratch") ?? "", /^blocked: /);
	assert.match(checkBash("env -u GH_REPO sh -c 'gh pr ready 1'", OWNED, zsEnv, inCua) ?? "", /^blocked: /);
	blocked("gh pr merge 1 -R trycua/cua --subject --help");
	blocked("gh pr merge 1 -R trycua/cua --body --disable-auto");
	assert.match(checkBash("ssh server 'gh pr merge 123'", OWNED, zsEnv, "/scratch") ?? "", /^blocked: /);
	allowed("gh pr merge 1 -R trycua/cua --squash --help");
	allowed("gh pr merge 1 -R trycua/cua --squash --disable-auto");
	allowed("gh pr merge --help", inCua);
	allowed("gh pr merge 6474 -R trycua/cua --disable-auto");
	assert.equal(checkBash("gh pr merge 7031 -R trycua/cua", new Set(), host, inCua), undefined);
});

test("readying a draft in a merge-job repo is blocked, through gh or GraphQL", () => {
	blocked("gh pr ready 7040 -R trycua/cua");
	blocked("gh pr ready 7040", inCua);
	blocked("gh pr ready https://github.com/trycua/cua/pull/7040");
	blocked(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	blocked(`gh api graphql -F query=@m.graphql -f q='enablePullRequestAutoMerge'`);
	blocked(`gh api -X POST graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	allowed("gh pr ready 7040 -R trycua/cua --undo");
	allowed("gh pr ready 33 -R injaneity/zstack", inCua);
	allowed(`gh api graphql -f query='{ repository(owner:"trycua",name:"cua"){ pullRequest(number:1){ isDraft } } }'`);
	allowed("gh pr create --draft --title x --body y", inCua);
	allowed("gh pr view 7040 --json isDraft", inCua);
});

test("a brief that allows merging lets the session ready its draft, and nothing else", () => {
	const ok = (c: string, cwd = "/zs") => assert.equal(checkBash(c, OWNED, host, cwd, true), undefined, c);
	const no = (c: string, cwd = "/zs") => assert.match(checkBash(c, OWNED, host, cwd, true) ?? "", /^blocked: /, c);
	ok("gh pr ready 7040 -R trycua/cua");
	ok("gh pr ready 7040", inCua);
	ok(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no("gh pr merge 7040 -R trycua/cua --squash");
	no("gh pr merge 7040 --auto", inCua);
	ok("gh run rerun 9 -R trycua/cua");
	no("gh api -X PUT repos/trycua/cua/pulls/7040/merge");
	no(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no("git push -f origin HEAD:main");
	assert.equal(readyAllowed("1"), true);
	for (const v of ["", "0", "true", "yes", " 1"]) assert.equal(readyAllowed(v), false, v);
	blocked("ONIGIRI_READY_ALLOWED=1 gh pr ready 7040 -R trycua/cua");
	blocked("env ONIGIRI_READY_ALLOWED=1 gh pr ready 7040", inCua);
	blocked("ONIGIRI_READY_ALLOWED=1 pi -p 'gh pr ready 7040 -R trycua/cua'");
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
	allowed("cat > /tmp/body.md <<EOF\nwe never run gh pr merge here\nEOF\ngh pr create --body-file /tmp/body.md", inCua);
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
	blocked("cat <<'EOF' | sh -s\ngh pr merge 1 -R trycua/cua\nEOF");
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
	assert.match(checkBash("cd /tmp/x extra || cd sync; gh pr merge 1", OWNED, host, "/cua") ?? "", /^blocked: /);
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

test("bin/hold-row is the sanctioned way to append a row relaying Zane's call; direct writes still name it", () => {
	const row = "preapprove | #7338 | no | " + "a".repeat(40) + " | deep; Zane 11 Nov 14:56 SGT (green at pin)";
	for (const c of [
		`bin/hold-row add '${row}'`,
		`~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row add "${row}"`,
		`ONIGIRI_ROOT=/srv/onigiri bin/hold-row add '${row}'`,
		`~/onigiri-vm/vm "~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row add '${row}'"`,
		`ssh vm 'bin/hold-row add "hold | 7400 | no | Zane 11 Nov 17:00 SGT: wait for bench"'`,
		`bin/hold-row retire '${row}' 'Zane 11 Nov 18:00 SGT: stale pin'`,
		`~/onigiri-vm/vm "~/.pi/agent/git/github.com/injaneity/zstack/bin/hold-row replace '${row}' '${row.replace("a".repeat(40), "b".repeat(40))}'"`,
	])
		allowed(c, "/srv/onigiri");
	blocked(`bin/hold-row add '${row}' > /srv/onigiri/hold-prs.txt`, "/srv/onigiri");
	blocked(`bin/hold-row add '${row}'; echo x >> /srv/onigiri/hold-prs.txt`, "/srv/onigiri");
	assert.match(checkBash("echo x >> /srv/onigiri/hold-prs.txt", OWNED, host) ?? "", /bin\/hold-row add/);
	assert.match(checkWrite("/srv/onigiri/hold-prs.txt", host) ?? "", /bin\/hold-row add/);
});

test("the real holds paths are this host's config, matched on the filesystem with symlinks followed", () => {
	assert.deepEqual(holdsPaths({ ONIGIRI_ROOT: "/srv/onigiri" }), ["/srv/onigiri/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"]);
	assert.deepEqual(holdsPaths({}), ["~/onigiri/hold-prs.txt", "/srv/onigiri/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"]);
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

test("the owned repo is config/policy.toml's merge.repo, and an unreadable policy refuses every merge and ready", () => {
	assert.deepEqual(OWNED, new Set(["trycua/cua"]));
	assert.deepEqual(mergeOwned(() => '{"merge": {"repo": "TryCua/Cua"}}'), new Set(["trycua/cua"]));
	for (const dump of [() => { throw new Error("policy: missing merge.repo"); }, () => "not json", () => "{}", () => '{"merge": {"repo": "cua"}}'])
		assert.equal(mergeOwned(dump), null);
	for (const c of ["gh pr merge 33 -R injaneity/zstack --squash", "gh pr ready 1", "gh api -X PUT repos/x/y/pulls/1/merge",
		"gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'"])
		assert.match(checkBash(c, null, host, "/zs") ?? "", /bin\/policy dump failed/, c);
	for (const c of ["gh pr view 1", "gh run rerun 9 --failed", "gh pr merge 1 --help", "git push"])
		assert.equal(checkBash(c, null, host, "/zs"), undefined, c);
});

test("closing a trycua/cua issue by hand needs a comment naming the PR that fixed it", () => {
	const no = (c: string, cwd = "/zs", h = host) => assert.match(checkBash(c, OWNED, h, cwd, false, ISSUES) ?? "", /^blocked: .*issue-close/, c);
	const ok = (c: string, cwd = "/zs", h = host) => assert.equal(checkBash(c, OWNED, h, cwd, false, ISSUES), undefined, c);
	assert.deepEqual(ISSUES, new Set(["trycua/cua"]));
	for (const c of [
		'gh issue close 6853 -R trycua/cua --comment "Fixed by #6928."',
		"gh issue close 6853 -R trycua/cua -c 'Fixed by #6928'",
		'gh issue close 6853 -R trycua/cua --comment="Fixed by https://github.com/trycua/cua/pull/6928"',
		'gh issue close https://github.com/trycua/cua/issues/6853 -c "Fixed by #6928" --reason completed',
		'gh pr close 3138 -R trycua/cua --comment "Replaced by #7587, which merged."',
		"gh issue close --help",
		"gh issue view 6853 -R trycua/cua",
		"gh issue comment 6853 -R trycua/cua --body 'x'",
		"gh pr close 1 -R injaneity/zstack",
	]) ok(c);
	ok('gh issue close 6853 --comment "Fixed by #6928"', inCua);
	for (const c of [
		"gh issue close 6853 -R trycua/cua",
		'gh issue close 6853 -R trycua/cua --comment "fixed on main"',
		'gh issue close 6853 -R trycua/cua --comment "see issue&#35;1"',
		'gh issue close 6853 -R trycua/cua --comment "Fixed by #6928" --reason "not planned"',
		'gh issue close 6853 -R trycua/cua --comment "Fixed by #6928" -r duplicate',
		'gh issue close 6853 -R trycua/cua --duplicate-of 6000 --comment "dup of #6000"',
		"gh issue close https://github.com/trycua/cua/issues/6853",
		'gh issue close 6853 -R trycua/cua --comment "$MSG"',
		"gh api -X PATCH repos/trycua/cua/issues/6853 -f state=closed",
		`gh api repos/trycua/cua/issues/6853 --input - <<< '{"state": "closed"}' -X PATCH`,
		`gh api graphql -f query='mutation { closeIssue(input: {issueId: "X"}) { clientMutationId } }'`,
		"gh api -X PATCH repos/trycua/cua/issues/5 --input payload.json",
		"gh api repos/trycua/cua/issues/5 --input=- -X PATCH",
		"gh api -X PATCH 'repos/trycua/cua/issues/5?x=/a' --input payload.json",
		"gh api -X PATCH 'repos/trycua/cua/issues/5#/a' -f state=closed",
		"gh api -X PATCH '/repos/trycua/cua/issues/5/?a=b' -f state=closed",
		'ssh vm "gh issue close 6853 -R trycua/cua"',
		"GH_REPO=trycua/cua gh issue close 6853",
	]) no(c);
	no("gh issue close 6853", inCua);
	no("cd /cua/wt && gh issue close 6853");
	no("gh issue close 6853", "/zs", { ...host, ghRepo: "trycua/cua" });
	ok("gh issue close 12 -R injaneity/zstack");
	ok("gh issue close 12", "/scratch");
	ok("gh api -X PATCH repos/injaneity/zstack/issues/12 -f state=closed");
	ok("gh api -X PATCH repos/trycua/cua/issues/6853 -f title=x");
	ok("gh api -X POST repos/trycua/cua/issues/6853/comments --input body.json");
	no("gh issue close 12", "/tmp/x");
	assert.match(checkBash("gh issue close 12 -R injaneity/zstack", OWNED, host, "/zs", false, null) ?? "", /^blocked: /);
	assert.equal(checkBash("gh issue close 6853 -R trycua/cua", OWNED, host, "/zs", false, new Set()), undefined);
	assert.equal(issuesOwned(() => "{}"), null);
	assert.equal(issuesOwned(() => { throw new Error("x"); }), null);
});

test("a PR or issue a review-only teammate opened is never closed, by the author gh reports", () => {
	const no = (c: string, h = host) => assert.match(checkBash(c, OWNED, h, "/zs", false, ISSUES) ?? "", /^blocked: .*, a review-only teammate\. /, c);
	const ok = (c: string, h = host) => assert.equal(checkBash(c, OWNED, h, "/zs", false, ISSUES), undefined, c);
	assert.deepEqual(TEAM, new Set(["f-trycua", "ddupont808"]));
	for (const c of [
		'gh issue close 7030 -R trycua/cua --comment "Fixed by #7147."',
		'gh pr close 7030 -R trycua/cua --comment "Replaced by #7147, which merged."',
		'gh pr close https://github.com/trycua/cua/pull/4468 -c "Replaced by #4560"',
		'gh issue close https://github.com/trycua/cua/issues/4468 --comment "Fixed by #4560"',
		"gh api -X PATCH repos/trycua/cua/pulls/7030 -f state=closed",
		'ssh vm "gh pr close 7030 -R trycua/cua"',
	]) no(c);
	for (const c of ["gh api -X PATCH repos/{owner}/{repo}/pulls/7030 -f state=closed", "gh pr close 7030 -c 'Replaced by #7147'"])
		assert.match(checkBash(c, OWNED, host, inCua, false, ISSUES) ?? "", /, a review-only teammate\. /, c);
	ok('gh pr close 3138 -R trycua/cua --comment "Replaced by #7587, which merged."');
	ok('gh issue close 6853 -R trycua/cua --comment "Fixed by #6928."');
	ok("gh pr close 7030 -R injaneity/zstack");
	ok("gh pr view 7030 -R trycua/cua");
	const refuse = (c: string, h: typeof host, why: RegExp) => assert.match(checkBash(c, OWNED, h, "/zs", false, ISSUES) ?? "", why, c);
	refuse('gh pr close 3138 -R trycua/cua -c "Replaced by #7587"', { ...host, reviewOnly: () => null }, /no hold-prs.txt could be read/);
	refuse('gh pr close 9999 -R trycua/cua -c "Replaced by #7587"', host, /could not tell/);
	refuse('gh pr close -R trycua/cua -c "Replaced by #7587"', host, /without a number/);
	refuse(`gh api graphql -f query='mutation { closePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`, host, /GraphQL close/);
	for (const c of ["gh api graphql -F query=@close.graphql", "gh api graphql --field=query=@q.graphql -F id=X"]) refuse(c, host, /GraphQL query read from a file/);
	for (const c of ["gh api graphql --input mutation.json", "gh api graphql --input=- < m.json", "gh api graphql -Fquery=@q"])
		refuse(c, host, /GraphQL query read from a file or stdin/);
	for (const c of ["gh api -X PATCH repos/trycua/cua/pulls/7030 -F state=@state.txt", "gh api -X PATCH repos/trycua/cua/pulls/7030 --field=state=@s",
		"gh api -X PATCH repos/trycua/cua/pulls/7030 -fstate=closed", "gh api -X PATCH repos/trycua/cua/pulls/7030 --raw-field=state=closed",
		"gh api -X PATCH repos/trycua/cua/pulls/7030 -F state=CLOSED", "gh api -X PATCH repos/trycua/cua/pulls/7030 --raw-field state=closed",
		"gh api -X PATCH repos/trycua/cua/pulls/7030 -Fstate=@-"])
		refuse(c, host, /, a review-only teammate\. /);
	ok("gh api graphql -f query='query { viewer { login } }'");
	ok("gh api -X PATCH repos/trycua/cua/pulls/7030 -F title=@t.txt");
	ok("gh api -X PATCH repos/trycua/cua/pulls/7030 -f state=open");
	assert.equal(reviewOnlyLogins([undefined, undefined]), null);
	assert.deepEqual(reviewOnlyLogins([undefined, ""]), new Set());
});

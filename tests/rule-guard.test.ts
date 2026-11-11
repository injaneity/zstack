// Tests for extensions/rule-guard/rules.ts. Run: node --test tests/rule-guard.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkBash, checkWrite, MERGE_OWNED, readyAllowed } from "../extensions/rule-guard/rules.ts";

const OWNED = MERGE_OWNED;
// a fake `git remote get-url origin`: any directory under /cua is a trycua/cua checkout
// and /scratch is a directory outside any checkout
const repoAt = (dir: string) => (dir.startsWith("/cua") ? "trycua/cua" : dir.startsWith("/zs") ? "injaneity/zstack" : dir.startsWith("/scratch") ? null : undefined);
// and a fake filesystem whose only holds files are these
const FILES = new Set(["/srv/onigiri/hold-prs.txt", "/zs/sync/hold-prs.txt"]);
// and a fake GitHub: trycua/cua run 7 was cancelled (its job 70 too), run 8 timed out, run 9 and job 90 failed
const CI: Record<string, string> = { "run 7": "cancelled", "job 70": "cancelled", "run 8": "timed_out", "run 9": "failure", "job 90": "failure" };
const ciConclusion = (repo: string, kind: string, id: string) => (repo === "trycua/cua" ? CI[`${kind} ${id}`] : undefined);
const host = { repoAt, isFile: (p: string) => FILES.has(p), ciConclusion };
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

test("a cancelled or timed-out run in a merge-job repo is rerun without asking; a real failure is not", () => {
	allowed("gh run rerun 7 -R trycua/cua --failed");
	allowed("gh run rerun 7 --failed", inCua);
	allowed("gh run rerun 8 -R trycua/cua");
	allowed("gh run rerun --job 70 -R trycua/cua");
	allowed("gh run rerun 9 --job=70 -R trycua/cua"); // the job is what reruns
	allowed("gh api -X POST repos/trycua/cua/actions/runs/7/rerun-failed-jobs");
	allowed("gh api -X POST repos/trycua/cua/actions/jobs/70/rerun");
	assert.equal(checkBash("gh run rerun 7 --failed", OWNED, { ...host, ghRepo: "trycua/cua" }, "/scratch"), undefined);
	blocked("gh run rerun 9 -R trycua/cua --failed"); // failed: Zane's call
	blocked("gh run rerun --job 90 -R trycua/cua");
	blocked("gh run rerun 7 --job 90 -R trycua/cua");
	blocked("gh api -X POST repos/trycua/cua/actions/runs/9/rerun-failed-jobs");
	// only the endpoint counts, not a field naming a cancelled run
	blocked("gh api -f x=repos/trycua/cua/actions/runs/7/rerun repos/trycua/cua/actions/runs/9/rerun");
	blocked("gh api repos/trycua/cua/actions/runs/9/rerun -f x=repos/trycua/cua/actions/runs/7/rerun");
	blocked("gh api -X POST repos/trycua/cua/actions/runs/7/rerun/../../9/rerun");
	allowed("gh api -H 'Accept: x' -X POST /repos/trycua/cua/actions/runs/7/rerun");
	blocked("gh run rerun 6 -R trycua/cua"); // conclusion unknown
	blocked("gh run rerun -R trycua/cua"); // no run named: gh would prompt for one
	blocked("gh run rerun $RUN -R trycua/cua");
	blocked("gh run rerun 7 --failed", "/tmp/x"); // repo can't be told
	allowed("cd /zs/wt || gh run rerun 7", inCua); // trycua/cua's run 7 is cancelled; zstack is not owned
	blocked("cd /zs/wt || gh run rerun 9", inCua);
	assert.match(checkBash("gh run rerun 7 -R trycua/cua", OWNED, { repoAt, isFile: () => false }) ?? "", /^blocked: /); // no GitHub lookup
	// cancelled CI never opens a merge
	blocked("gh run rerun 7 -R trycua/cua && gh pr merge 1 -R trycua/cua");
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
	// a local shell inherits the prefix it was started with
	assert.match(checkBash("GH_REPO=trycua/cua bash -c 'gh pr merge 1'", OWNED, zsEnv, "/scratch") ?? "", /^blocked: /);
	assert.match(checkBash("env -u GH_REPO sh -c 'gh pr ready 1'", OWNED, zsEnv, inCua) ?? "", /^blocked: /);
	blocked("gh pr merge 1 -R trycua/cua --subject --help");
	blocked("gh pr merge 1 -R trycua/cua --body --disable-auto");
	// the session's GH_REPO does not reach a remote shell, whose directory is unknown
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
	no("gh run rerun 9 -R trycua/cua");
	no("gh api -X PUT repos/trycua/cua/pulls/7040/merge");
	no(`gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { markPullRequestReadyForReview(input: {pullRequestId: "X"}) { clientMutationId } mergePullRequest(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no(`gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {pullRequestId: "X"}) { clientMutationId } }'`);
	no("git push -f origin HEAD:main");
	// only the launcher's exact flag counts; anything else is the default, blocked
	assert.equal(readyAllowed("1"), true);
	for (const v of ["", "0", "true", "yes", " 1"]) assert.equal(readyAllowed(v), false, v);
	// a flag on the command is not the session's: the guard reads the env it was loaded with
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
	blocked("cat <<EOF\nx"+"\ngit push -f"); // no closing line: nothing is stripped
	blocked("echo '<<EOF'\ngit push -f\nEOF"); // a quoted << is text, so the next line runs
	blocked("cat <<EOF\n$(git push -f)\nEOF"); // an unquoted body's substitutions run
	blocked("echo hi # <<EOF\ngit push --force\nEOF"); // a << in a comment is text
	blocked("cat <<'EOF' | bash\ngit push -f\nEOF"); // piped into a shell, the body runs
	blocked("cat <<EOF.txt\ndata\nEOF.txt\ngit push -f\nEOF"); // the delimiter is the whole word
	blocked("cat <<EOF \\\n | bash\ngit push -f\nEOF"); // a continued line can pipe the body into a shell
	blocked("echo \\<<EOF\ngit push -f\nEOF"); // an escaped < is no heredoc
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
	assert.equal(checkWrite("/srv/onigiri/notes.md"), undefined);
	assert.equal(checkWrite("hold-prs.txt.md"), undefined);
});

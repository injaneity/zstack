import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

for (const k of ["ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"]) delete process.env[k];

const root = new URL("..", import.meta.url).pathname;
const hooks = JSON.parse(fs.readFileSync(`${root}hooks/hooks.json`, "utf8")).hooks;
const command = (event: string) => hooks[event][0].hooks[0].command.replaceAll("${CLAUDE_PLUGIN_ROOT}", root.replace(/\/$/, ""));
const repo = spawnSync("python3", ["-I", `${root}queue.py`, "get", "merge.repo"], { encoding: "utf8" }).stdout.trim();

function guard(call: unknown, env: Record<string, string> = {}) {
	const text = typeof call === "string" ? call : JSON.stringify(call);
	return spawnSync("sh", ["-c", command("PreToolUse")], { input: text, encoding: "utf8", env: { ...process.env, ...env } });
}

test("the Claude Code hook refuses what the pi extension refuses, with exit 2 and the reason alone on stderr", () => {
	const merge = guard({ tool_name: "Bash", tool_input: { command: `gh pr merge 12 --repo ${repo}` }, cwd: "/tmp" });
	assert.equal(merge.status, 2);
	assert.match(merge.stderr, /^blocked: `gh pr merge`/);
	assert.equal(merge.stderr.trim().split("\n").length, 1);
	const label = guard({ tool_name: "Bash", tool_input: { command: `gh pr edit 12 --repo ${repo} --remove-label hold` }, cwd: "/tmp" });
	assert.equal(label.status, 2);
	assert.match(label.stderr, /only the owner sets/);
	for (const tool of ["Write", "Edit", "MultiEdit"]) {
		const file = guard({ tool_name: tool, tool_input: { file_path: "/tmp/root/queue.json" }, cwd: "/tmp" });
		assert.equal(file.status, 2, tool);
		assert.match(file.stderr, /the queue's file/);
	}
});

test("an allowed call passes in silence", () => {
	for (const call of [{ tool_name: "Bash", tool_input: { command: "ls" } }, { tool_name: "Write", tool_input: { file_path: "/tmp/x.txt" } }]) {
		const r = guard({ ...call, cwd: "/tmp" });
		assert.equal(r.status, 0);
		assert.equal(r.stdout + r.stderr, "");
	}
});

test("`gh pr ready` is refused whatever the environment says", () => {
	const call = { tool_name: "Bash", tool_input: { command: `gh pr ready 12 --repo ${repo}` }, cwd: "/tmp" };
	assert.equal(guard(call).status, 2);
	assert.equal(guard(call, { ONIGIRI_READY_ALLOWED: "1" }).status, 2);
});

test("input the hook cannot read is refused, not waved through", () => {
	assert.equal(guard("not json").status, 2);
});

test("the hook covers every tool that runs a command, writes a file or starts a helper, and session start prints the invariants", () => {
	assert.deepEqual(hooks.PreToolUse[0].matcher.split("|").sort(), ["Agent", "Bash", "Edit", "MultiEdit", "NotebookEdit", "Task", "Write", "mcp__.*__delegate_task"]);
	const rules = spawnSync("sh", ["-c", command("SessionStart")], { encoding: "utf8" });
	assert.equal(rules.status, 0);
	assert.match(rules.stdout, /^## Invariants/m);
	const attach = hooks.SessionStart[0].hooks[1].command.replaceAll("${CLAUDE_PLUGIN_ROOT}", root.replace(/\/$/, ""));
	assert.match(attach, /queue\.py" attach /);
	assert.equal(spawnSync("sh", ["-c", attach], { encoding: "utf8", cwd: os.tmpdir() }).status, 0);
});

test("the plugin and the pi package are one version", () => {
	const plugin = JSON.parse(fs.readFileSync(`${root}.claude-plugin/plugin.json`, "utf8"));
	const pkg = JSON.parse(fs.readFileSync(`${root}package.json`, "utf8"));
	assert.equal(plugin.version, pkg.version);
	assert.equal(plugin.name, pkg.name);
});

test("a machine with its own config directory gets its own rules and its own guarded repo", () => {
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), "zstack-config-"));
	try {
		fs.mkdirSync(path.join(dir, "machines"));
		fs.mkdirSync(path.join(dir, "repos", "acme", "widgets"), { recursive: true });
		fs.writeFileSync(path.join(dir, "machines", "default.toml"), fs.readFileSync(`${root}config/machines/default.toml`, "utf8").replaceAll(repo, "acme/widgets"));
		fs.writeFileSync(path.join(dir, "repos", "acme", "widgets", "policy.toml"), fs.readFileSync(`${root}config/repos/${repo}/policy.toml`, "utf8").replaceAll(repo, "acme/widgets"));
		const env = { ZSTACK_CONFIG: dir };
		const merge = (r: string) => guard({ tool_name: "Bash", tool_input: { command: `gh pr merge 12 --repo ${r}` }, cwd: "/tmp" }, env);
		assert.equal(merge("acme/widgets").status, 2);
		assert.match(merge("acme/widgets").stderr, /in acme\/widgets/);
		assert.equal(merge(repo).status, 0);
		const rules = spawnSync("sh", ["-c", command("SessionStart")], { encoding: "utf8", env: { ...process.env, ...env } });
		assert.match(rules.stdout, /acme\/widgets/);
		assert.doesNotMatch(rules.stdout, new RegExp(repo));
	} finally {
		fs.rmSync(dir, { recursive: true });
	}
});

test("a thread the queue has not recorded is recorded by its first guarded call, and nothing is published or delegated while it cannot be", () => {
	const dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "zstack-attach-")));
	try {
		const [base, tree] = [path.join(dir, "root"), path.join(dir, "root", "trees", "a")];
		fs.mkdirSync(path.join(dir, "machines"));
		fs.mkdirSync(tree, { recursive: true });
		fs.cpSync(`${root}config/repos`, path.join(dir, "repos"), { recursive: true });
		const machine = fs.readFileSync(`${root}config/machines/default.toml`, "utf8").replace(/^root = .*$/m, `root = "${base}"`);
		assert.match(machine, new RegExp(`root = "${base}"`));
		fs.writeFileSync(path.join(dir, "machines", "default.toml"), machine);
		const item = { kind: "task", n: null, preapproved: false, readonly: false, task: "x", title: "x", thread: null, worktree: tree, pr: null, at: "2026-01-01T00:00:00Z" };
		const file = path.join(base, "queue.json");
		fs.writeFileSync(file, JSON.stringify({ running: { a: item }, waiting: {}, tasks: {}, verdicts: {}, done: [] }));
		const run = (call: unknown, thread: string) =>
			spawnSync("sh", ["-c", command("PreToolUse")], { input: JSON.stringify(call), encoding: "utf8", cwd: tree, env: { ...process.env, ZSTACK_CONFIG: dir, T3CODE_THREAD_ID: thread } });
		const push = { tool_name: "Bash", tool_input: { command: "git push origin x" }, cwd: tree };
		const model = spawnSync("python3", ["-I", `${root}queue.py`, "seat", "verifier"], { encoding: "utf8" }).stdout.trim().split(" ")[1];
		const helper = { tool_name: "mcp__t3-code__delegate_task", tool_input: { role: "review", mode: "wait", target: { model } }, cwd: tree };
		assert.equal(run(push, "t-1").status, 0);
		assert.equal(JSON.parse(fs.readFileSync(file, "utf8")).running.a.thread, "t-1");
		assert.equal(run(helper, "t-1").status, 0);
		fs.writeFileSync(file, "not json");
		assert.match(run(push, "t-1").stderr, /or the queue could not be read/);
		assert.match(run(helper, "t-1").stderr, /could not say whether this thread is recorded for its item, so the verifier does not start/);
	} finally {
		fs.rmSync(dir, { recursive: true });
	}
});

test("a config directory that is not there refuses every guarded call instead of falling back to the package's", () => {
	const env = { ZSTACK_CONFIG: "/nonexistent/zstack-config" };
	assert.equal(guard({ tool_name: "Bash", tool_input: { command: `gh pr merge 12 --repo ${repo}` }, cwd: "/tmp" }, env).status, 2);
	assert.notEqual(spawnSync("sh", ["-c", command("SessionStart")], { encoding: "utf8", env: { ...process.env, ...env } }).status, 0);
});

test("a helper runs on the model the config gives its role, never as a subagent of the coding model", () => {
	const seat = (role: string) => spawnSync("python3", ["-I", `${root}queue.py`, "seat", role], { encoding: "utf8" }).stdout.trim().split(" ")[1];
	for (const type of ["zstack:verifier", "zstack:explorer", "verifier"]) {
		const r = guard({ tool_name: "Agent", tool_input: { subagent_type: type, prompt: "x" }, cwd: "/tmp" });
		assert.equal(r.status, 2, type);
		assert.match(r.stderr, /runs on its own model/);
	}
	assert.equal(guard({ tool_name: "Agent", tool_input: { subagent_type: "Explore", prompt: "x" }, cwd: "/tmp" }).status, 0);
	const delegate = (role: string, model?: string, mode: string | null = "wait") =>
		guard({ tool_name: "mcp__t3-code__delegate_task", tool_input: { task: "x", role, mode, target: model ? { model } : undefined }, cwd: "/tmp" });
	for (const mode of ["async", null]) {
		const r = delegate("review", seat("verifier"), mode);
		assert.equal(r.status, 2, String(mode));
		assert.match(r.stderr, /with mode "wait"/);
	}
	assert.equal(delegate("implementation", undefined, "async").status, 0);
	assert.equal(delegate("review", seat("verifier")).status, 0);
	assert.equal(delegate("research", seat("explorer")).status, 0);
	const wrong = delegate("review", "some-other-model");
	assert.equal(wrong.status, 2);
	assert.match(wrong.stderr, new RegExp(`the verifier runs on ${seat("verifier")}, not some-other-model`));
	assert.equal(delegate("review").status, 2);
	assert.equal(delegate("implementation").status, 0);
});

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

const root = new URL("..", import.meta.url).pathname;
const hooks = JSON.parse(fs.readFileSync(`${root}hooks/hooks.json`, "utf8")).hooks;
const command = (event: string) => hooks[event][0].hooks[0].command.replaceAll("${CLAUDE_PLUGIN_ROOT}", root.replace(/\/$/, ""));
const repo = spawnSync("python3", ["-I", `${root}bin/policy`, "get", "merge.repo"], { encoding: "utf8" }).stdout.trim();

function guard(call: unknown, env: Record<string, string> = {}) {
	const text = typeof call === "string" ? call : JSON.stringify(call);
	return spawnSync("sh", ["-c", command("PreToolUse")], { input: text, encoding: "utf8", env: { ...process.env, ONIGIRI_READY_ALLOWED: "", ...env } });
}

test("the Claude Code hook refuses what the pi extension refuses, with exit 2 and the reason alone on stderr", () => {
	const merge = guard({ tool_name: "Bash", tool_input: { command: `gh pr merge 12 --repo ${repo}` }, cwd: "/tmp" });
	assert.equal(merge.status, 2);
	assert.match(merge.stderr, /^blocked: `gh pr merge`/);
	assert.equal(merge.stderr.trim().split("\n").length, 1);
	for (const tool of ["Write", "Edit", "MultiEdit"]) {
		const holds = guard({ tool_name: tool, tool_input: { file_path: "~/zstack/hold-prs.txt" }, cwd: "/tmp" });
		assert.equal(holds.status, 2, tool);
		assert.match(holds.stderr, /holds file/);
	}
});

test("an allowed call passes in silence", () => {
	for (const call of [{ tool_name: "Bash", tool_input: { command: "ls" } }, { tool_name: "Write", tool_input: { file_path: "/tmp/x.txt" } }]) {
		const r = guard({ ...call, cwd: "/tmp" });
		assert.equal(r.status, 0);
		assert.equal(r.stdout + r.stderr, "");
	}
});

test("`gh pr ready` follows ONIGIRI_READY_ALLOWED as it does under pi", () => {
	const call = { tool_name: "Bash", tool_input: { command: `gh pr ready 12 --repo ${repo}` }, cwd: "/tmp" };
	assert.equal(guard(call).status, 2);
	assert.equal(guard(call, { ONIGIRI_READY_ALLOWED: "1" }).status, 0);
});

test("input the hook cannot read is refused, not waved through", () => {
	assert.equal(guard("not json").status, 2);
});

test("the hook covers every tool that runs a command or writes a file, and session start prints the Hard rules", () => {
	assert.deepEqual(hooks.PreToolUse[0].matcher.split("|").sort(), ["Bash", "Edit", "MultiEdit", "NotebookEdit", "Write"]);
	const rules = spawnSync("sh", ["-c", command("SessionStart")], { encoding: "utf8" });
	assert.equal(rules.status, 0);
	assert.match(rules.stdout, /^## Hard rules/m);
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

test("a config directory that is not there refuses every guarded call instead of falling back to the package's", () => {
	const env = { ZSTACK_CONFIG: "/nonexistent/zstack-config" };
	assert.equal(guard({ tool_name: "Bash", tool_input: { command: `gh pr merge 12 --repo ${repo}` }, cwd: "/tmp" }, env).status, 2);
	assert.notEqual(spawnSync("sh", ["-c", command("SessionStart")], { encoding: "utf8", env: { ...process.env, ...env } }).status, 0);
});

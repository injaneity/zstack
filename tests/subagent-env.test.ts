import assert from "node:assert/strict";
import { test } from "node:test";
import { PARENT_THREAD_VAR, THREAD_VAR } from "../hooks/rules.ts";
import { childEnv } from "../pi/subagent/env.ts";

test("a child carries its own thread id and a link to its parent's, so its end releases nothing and its verdict is not the parent's", () => {
	const env = childEnv({ [THREAD_VAR]: "t-parent", PATH: "/bin" }, 0, "/tmp/gh", "s-1-verifier-abc");
	assert.ok(env[THREAD_VAR]);
	assert.notEqual(env[THREAD_VAR], "t-parent");
	assert.equal(env[PARENT_THREAD_VAR], "t-parent");
	assert.notEqual(childEnv({ [THREAD_VAR]: "t-parent" }, 0, "/tmp/gh", "s-2-explorer-def")[THREAD_VAR], env[THREAD_VAR]);
});

test("a child of a session with no thread id still has its own, and no parent link", () => {
	const env = childEnv({ [PARENT_THREAD_VAR]: "stale", PATH: "/bin" }, 0, "/tmp/gh", "s-3-verifier-ghi");
	assert.ok(env[THREAD_VAR]);
	assert.equal(env[PARENT_THREAD_VAR], undefined);
});

test("a child gets no forge credentials and one more level of depth", () => {
	const env = childEnv({ GH_TOKEN: "x", GITHUB_TOKEN: "y", ANTHROPIC_API_KEY: "k", PATH: "/bin" }, 0, "/tmp/gh", "s-4");
	assert.equal(env.GH_TOKEN, undefined);
	assert.equal(env.GITHUB_TOKEN, undefined);
	assert.equal(env.ANTHROPIC_API_KEY, "k");
	assert.equal(env.GH_CONFIG_DIR, "/tmp/gh");
	assert.equal(env.PI_SUBAGENT_DEPTH, "1");
});

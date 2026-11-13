import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { boundBash, idleSeconds } from "../extensions/tool-guard/index.ts";

const root = new URL("..", import.meta.url).pathname;

test("a bash call with no timeout gets run.idle_min less the supervisor's minute; a declared one is kept", () => {
	const idle = Number(execFileSync("python3", ["-I", `${root}bin/policy`, "get", "run.idle_min"], { encoding: "utf8" }).trim());
	const s = idleSeconds();
	assert.equal(s, (idle - 1) * 60);
	const none: { timeout?: unknown } = {};
	boundBash(none, s);
	assert.equal(none.timeout, (idle - 1) * 60);
	const own = { timeout: 30 };
	boundBash(own, s);
	assert.equal(own.timeout, 30);
	const bad: { timeout?: unknown } = { timeout: 0 };
	boundBash(bad, s);
	assert.equal(bad.timeout, (idle - 1) * 60);
});

test("an unreadable policy bounds nothing", () => {
	assert.equal(idleSeconds(() => { throw new Error("no policy"); }), undefined);
	assert.equal(idleSeconds(() => "1"), undefined);
	const input: { timeout?: unknown } = {};
	boundBash(input, undefined);
	assert.equal(input.timeout, undefined);
});

// Tests for extensions/tool-guard/find-guard.ts. Run: node --test tests/find-guard.test.ts
import assert from "node:assert/strict";
import { test } from "node:test";
import { checkFind } from "../extensions/tool-guard/find-guard.ts";

const HOME = "/Users/administrator";
const blocked = (c: string) => assert.match(checkFind(c, HOME) ?? "", /^blocked: /, c);
const allowed = (c: string) => assert.equal(checkFind(c, HOME), undefined, c);

test("the two finds from the 196-minute session are blocked", () => {
	blocked("find / -name cmake -type f 2>/dev/null | head");
	blocked("find /tmp /Users/administrator -name cmake 2>/dev/null");
});

test("whole-disk and whole-home finds are blocked however they are written", () => {
	for (const c of [
		"find /",
		"find / -maxdepth 6 -name x",
		"sudo find / -name x",
		"timeout 600 find / -name x",
		"/usr/bin/find /usr -name cmake",
		"cd /x && find ~ -name '*.dylib'",
		"find $HOME -name cmake",
		"find ~/ -name cmake",
		"find /home -name x",
		"find /Users/someone -name x",
		"find -L /opt -name x",
		"ls; find /opt/homebrew -name cmake",
		"x=$(find / -name cmake | head -1)",
		"echo hi | xargs find / -name",
		"find /usr/.. -name cmake",
		"find /usr//local -name cmake",
		"find /opt/./homebrew/ -name cmake",
		"find / -exec echo -maxdepth 2 \\;",
		'echo "$(find / -name cmake)"',
		'echo "`find ~ -name cmake`"',
		"find ${HOME} -name cmake",
		"cd / && find . -name cmake",
		"cd ~ && find -name cmake",
		"cd /usr; find local -name cmake",
		"{ find / -name x; }",
	])
		blocked(c);
});

test("scoped or shallow finds run", () => {
	for (const c of [
		"find . -name '*.rs'",
		"find",
		"find src tests -type f",
		"find /Users/administrator/onigiri/review/x -name '*.log'",
		"find ~/project -name x",
		"find / -maxdepth 1 -type d",
		"find ~ -maxdepth 2 -name '.zshrc'",
		"find /tmp/build-123 -name x",
		"find . -maxdepth 1 -exec ls {} \\;",
		"cd /tmp/build-1 && find . -name '*.o'",
		"cd src && find . -name x",
		"find ${HOME}/project -name x",
		"{ find . -name x; }",
		'echo "find / is blocked"',
		'echo "$(pwd) then find / in prose"',
		'echo "`date` find ~ text"',
		"echo 'find / -name x' \"$(pwd)\"",
		"which cmake || command -v cmake",
		"echo 'find / -name x'",
		"grep -rn 'find /' docs",
		"git log --format=%H | head",
	])
		allowed(c);
});

test("the refusal says what to do instead", () => {
	assert.match(checkFind("find / -name cmake", HOME) ?? "", /command -v .*brew --prefix/);
});

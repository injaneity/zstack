import { check } from "../extensions/rule-guard/host.ts";
import { readyAllowed } from "../extensions/rule-guard/rules.ts";

let text = "";
for await (const chunk of process.stdin) text += chunk;
let call: { tool_name?: unknown; tool_input?: { command?: unknown; file_path?: unknown; notebook_path?: unknown }; cwd?: unknown };
try {
	call = JSON.parse(text);
} catch {
	console.error("zstack rule-guard: the hook input is not JSON, so the call is refused");
	process.exit(2);
}
const input = call.tool_input ?? {};
const cwd = typeof call.cwd === "string" ? call.cwd : undefined;
const reason =
	call.tool_name === "Bash" ? check("bash", String(input.command ?? ""), cwd, readyAllowed()) :
	check("write", String(input.file_path ?? input.notebook_path ?? ""), cwd, false);
if (reason) {
	console.error(reason);
	process.exit(2);
}

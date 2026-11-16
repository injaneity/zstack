import { check, checkDelegate } from "../extensions/rule-guard/host.ts";
import { readyAllowed } from "../extensions/rule-guard/rules.ts";

let text = "";
for await (const chunk of process.stdin) text += chunk;
let call: { tool_name?: unknown; tool_input?: Record<string, unknown>; cwd?: unknown };
try {
	call = JSON.parse(text);
} catch {
	console.error("zstack rule-guard: the hook input is not JSON, so the call is refused");
	process.exit(2);
}
const input = call.tool_input ?? {};
const cwd = typeof call.cwd === "string" ? call.cwd : undefined;
const tool = String(call.tool_name ?? "");
const reason =
	tool === "Bash" ? check("bash", String(input.command ?? ""), cwd, readyAllowed()) :
	tool === "Agent" || tool === "Task" || tool.endsWith("delegate_task") ? checkDelegate(tool.endsWith("delegate_task") ? "delegate" : tool, input) :
	check("write", String(input.file_path ?? input.notebook_path ?? ""), cwd, false);
if (reason) {
	console.error(reason);
	process.exit(2);
}

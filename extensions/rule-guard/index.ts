import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { check, release } from "./host.ts";
import { readyAllowed } from "./rules.ts";

export default function (pi: ExtensionAPI) {
	const ready = readyAllowed();
	delete process.env.ONIGIRI_READY_ALLOWED;
	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: unknown; path?: unknown };
		const reason =
			event.toolName === "bash" ? check("bash", String(input.command ?? ""), ctx.cwd, ready) :
			event.toolName === "write" || event.toolName === "edit" ? check("write", String(input.path ?? ""), ctx.cwd, ready) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
	pi.on("agent_end", () => release());
}

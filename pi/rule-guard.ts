import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { attach, check, release } from "../hooks/host.ts";

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: unknown; path?: unknown };
		const reason =
			event.toolName === "bash" ? check("bash", String(input.command ?? ""), ctx.cwd) :
			event.toolName === "write" || event.toolName === "edit" ? check("write", String(input.path ?? ""), ctx.cwd) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
	pi.on("before_agent_start", () => attach());
	pi.on("agent_end", () => release());
	pi.on("session_shutdown", () => release());
}

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { check, release } from "../hooks/host.ts";

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", (event, ctx) => {
		const reason = event.toolName === "bash" ? check(String((event.input as { command?: unknown }).command ?? ""), ctx.cwd) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
	pi.on("agent_end", () => release());
	pi.on("session_shutdown", () => release());
}

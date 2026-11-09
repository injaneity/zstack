// Vendored from pi 1.4.0, @earendil-works/pi-coding-agent examples/extensions/subagent/index.ts.
// Copyright (c) 2025 Mario Zechner. MIT License, see LICENSE.
// zstack changes, and only these: (a) the child's system prompt is its role prompt from agents/<name>.md plus a
// line placing it in the pipeline, and never the session's Hard rules;
// (b) PI_SUBAGENT_DEPTH >= 1 skips registering the tool, and children get depth + 1; (c) children
// are told not to sleep or poll; (d) only the user's agents load, never a project's; (e) children get an environment without forge or git credentials; (f) a session_start
// check reports a missing or stale agent file or a missing gh login, and offers the fix only with a UI; (g) a child's
// provider and model come from config/models.json by agent name, with an auth preflight that falls back to the
// parent model with a warning; (h) children save a session named role:<agent>, under sessions/subagents/, so
// bin/check-models can read what they ran on. Upstream's parallel and chain modes and its custom renderers are removed; pi's default
// tool renderer shows the result.
// See docs/SUBAGENT.md.

/**
 * Subagent Tool - Delegate tasks to specialized agents
 *
 * Spawns a separate `pi` process for each subagent invocation,
 * giving it an isolated context window.
 *
 * One call: { agent: "name", task: "..." }
 *
 * Uses JSON mode to capture structured output from subagents.
 */

import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { AgentToolResult, ThinkingLevel } from "@earendil-works/pi-agent-core";
import type { Message } from "@earendil-works/pi-ai";
import { type ExtensionAPI, type ExtensionContext, getAgentDir, withFileMutationQueue } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { type AgentConfig, discoverAgents } from "./agents.ts";
import { buildChildPrompt } from "./prompt.ts";

const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const MODELS_CONFIG = path.join(PACKAGE_DIR, "config", "models.json");
// Children are saved apart from top-level sessions, so resuming or `pi --continue` never picks one up.
const CHILD_SESSIONS = path.join(getAgentDir(), "sessions", "subagents");
// Unset means a top-level session. A value that is set but not a non-negative integer fails closed.
const rawDepth = process.env.PI_SUBAGENT_DEPTH ?? "";
const SUBAGENT_DEPTH = rawDepth === "" ? 0 : /^\d+$/.test(rawDepth) ? Number(rawDepth) : 1;
// Forge and git credentials are removed from the child's environment. Model-provider credentials stay,
// because the child needs them to call its model.
const CREDENTIAL_ENV = /TOKEN|SECRET|PASSW|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTH|ASKPASS/i;
const MODEL_PROVIDER_ENV =
	/^(ANTHROPIC|CLAUDE_CODE|OPENAI|AZURE_OPENAI|AI_GATEWAY|GEMINI|GOOGLE|GCLOUD|AWS|MISTRAL|GROQ|CEREBRAS|XAI|OPENROUTER|DEEPSEEK|MOONSHOT|KIMI|MINIMAX|ZAI|HF|FIREWORKS|TOGETHER|BASETEN|OPENCODE|META|CLOUDFLARE|XIAOMI|QWEN|ANT_LING|NVIDIA|RADIUS|TYPESAFE)_/;

function childEnv(ghConfigDir: string): NodeJS.ProcessEnv {
	const env: NodeJS.ProcessEnv = {};
	for (const [key, value] of Object.entries(process.env)) {
		if (CREDENTIAL_ENV.test(key) && !MODEL_PROVIDER_ENV.test(key)) continue;
		env[key] = value;
	}
	// gh keeps its token in its config dir, and git's credential helper asks gh, so an empty dir logs both out.
	return { ...env, GH_CONFIG_DIR: ghConfigDir, GIT_TERMINAL_PROMPT: "0", PI_SUBAGENT_DEPTH: String(SUBAGENT_DEPTH + 1) };
}

interface Seat {
	provider: string;
	model: string;
}

// The seat config/models.json gives an agent, keyed by agent name. A role whose value is a list has one seat
// per variant (1-based), as the two designers do. No entry means the agent runs on the parent's model.
function seatFor(agent: string, variant: number | undefined): { seat?: Seat; label: string; error?: string } {
	let roles: Record<string, unknown>;
	try {
		roles = JSON.parse(fs.readFileSync(MODELS_CONFIG, "utf-8")).roles ?? {};
	} catch (error) {
		return { label: agent, error: `cannot read ${MODELS_CONFIG}: ${(error as Error).message}` };
	}
	const isSeat = (v: any): v is Seat => typeof v?.provider === "string" && typeof v?.model === "string";
	const entry = Object.hasOwn(roles, agent) ? roles[agent] : undefined;
	if (entry === undefined) {
		return variant ? { label: agent, error: `${agent} has no seats in ${MODELS_CONFIG}; drop variant` } : { label: agent };
	}
	if (Array.isArray(entry)) {
		if (!variant || variant > entry.length)
			return { label: agent, error: `${agent} has ${entry.length} seats in ${MODELS_CONFIG}; pass variant 1 to ${entry.length}` };
		const seat = entry[variant - 1];
		return isSeat(seat) ? { seat, label: `${agent}:${variant}` } : { label: agent, error: `${agent} seat ${variant} in ${MODELS_CONFIG} needs provider and model` };
	}
	if (variant) return { label: agent, error: `${agent} has one seat in ${MODELS_CONFIG}; drop variant` };
	return isSeat(entry) ? { seat: entry, label: agent } : { label: agent, error: `${agent} in ${MODELS_CONFIG} needs provider and model` };
}

// Resolves the seat's credentials in this process, refreshing an expired OAuth token, so a dead login is
// caught before the child starts. Returns why the seat can't be used, or undefined when it can. A model
// missing from pi's catalog is not a failure: pi runs it as a custom id, so only the provider's auth is checked.
async function seatProblem(ctx: ExtensionContext, seat: Seat): Promise<string | undefined> {
	try {
		const model = ctx.modelRegistry.find(seat.provider, seat.model);
		if (model) {
			const auth = await ctx.modelRegistry.getApiKeyAndHeaders(model);
			return auth.ok ? undefined : auth.error;
		}
		if (!ctx.modelRegistry.getProvider(seat.provider)) return `provider ${seat.provider} not found`;
		return (await ctx.modelRegistry.getProviderAuth(seat.provider)) ? undefined : `no credentials for ${seat.provider}`;
	} catch (error) {
		return (error as Error).message ?? String(error);
	}
}

// Session-start check. A gap with fix=true is closed by bin/setup.sh, which never overwrites a file and
// never prompts without a terminal. gh is only a warning, and runs in this process, never a child's env.
async function checkDependencies(pi: ExtensionAPI, ctx: ExtensionContext): Promise<void> {
	// Only the TUI prompts. In RPC mode hasUI is true, but pi has not started reading replies during
	// session_start, so a confirm there would never settle.
	const tui = ctx.hasUI && ctx.mode === "tui";
	const say = (line: string, level: "info" | "warning" = "warning") =>
		tui ? ctx.ui.notify(line, level) : console.error(line);
	const agentsDir = path.join(getAgentDir(), "agents");
	const setup = path.join(PACKAGE_DIR, "bin", "setup.sh");
	const gaps: { message: string; remedy: string; fix?: boolean }[] = [];
	const shipped = fs.readdirSync(path.join(PACKAGE_DIR, "agents")).filter((f) => f.endsWith(".md"));
	const absent = shipped.filter((f) => !fs.existsSync(path.join(agentsDir, f)));
	if (absent.length) gaps.push({ message: `agent files missing from ${agentsDir}: ${absent.join(", ")}`, remedy: `bash ${setup}`, fix: true });
	// install-agents.sh never overwrites, so an installed role prompt can lag the package's. Only a warning: the copy may be edited on purpose.
	const read = (f: string) => fs.readFileSync(f, "utf-8");
	const stale = shipped.filter((f) => !absent.includes(f) && read(path.join(agentsDir, f)) !== read(path.join(PACKAGE_DIR, "agents", f)));
	if (stale.length) gaps.push({ message: `agent files in ${agentsDir} differ from the package's: ${stale.join(", ")}`, remedy: `bash ${path.join(PACKAGE_DIR, "bin", "install-agents.sh")} --force (overwrites them)` });
	const remotes = await pi.exec("git", ["-C", ctx.cwd, "remote", "-v"], { timeout: 5000 }).catch(() => undefined);
	if (remotes?.code === 0 && remotes.stdout.includes("github.com")) {
		const gh = await pi.exec("gh", ["auth", "status"], { timeout: 10000 }).catch(() => undefined);
		if (gh?.code !== 0) gaps.push({ message: "gh is not installed or not logged in; PR steps will fail", remedy: "gh auth login" });
	}
	// No UI means nobody can answer: never prompt, never block, print the remedy and go on.
	if (!tui || !gaps.some((g) => g.fix)) return gaps.forEach((g) => say(`zstack: ${g.message}. Fix: ${g.remedy}`));
	gaps.filter((g) => !g.fix).forEach((g) => say(`zstack: ${g.message}. Fix: ${g.remedy}`));
	const fixable = gaps.filter((g) => g.fix).map((g) => g.message);
	if (!(await ctx.ui.confirm("Run zstack's bin/setup.sh?", `${fixable.join(".\n")}.\nIt copies what is absent and overwrites nothing.`))) return;
	const r = await pi.exec("bash", [setup], { timeout: 30000, cwd: ctx.cwd });
	ctx.ui.notify(r.code === 0 ? `zstack: setup.sh ran.\n${r.stdout.trim()}` : `zstack: setup.sh failed: ${r.stderr.trim()}`, r.code === 0 ? "info" : "error");
}

interface SingleResult {
	agent: string;
	task: string;
	exitCode: number;
	messages: Message[];
	stderr: string;
	model?: string;
	stopReason?: string;
	errorMessage?: string;
}

interface SubagentDetails {
	results: SingleResult[];
}

function getFinalOutput(messages: Message[]): string {
	for (let i = messages.length - 1; i >= 0; i--) {
		const msg = messages[i];
		if (msg.role === "assistant") {
			for (const part of msg.content) {
				if (part.type === "text") return part.text;
			}
		}
	}
	return "";
}

function isFailedResult(result: SingleResult): boolean {
	return result.exitCode !== 0 || result.stopReason === "error" || result.stopReason === "aborted";
}

function getResultOutput(result: SingleResult): string {
	if (isFailedResult(result)) {
		return result.errorMessage || result.stderr || getFinalOutput(result.messages) || "(no output)";
	}
	return getFinalOutput(result.messages) || "(no output)";
}

async function writePromptToTempFile(agentName: string, prompt: string): Promise<{ dir: string; filePath: string }> {
	const tmpDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "pi-subagent-"));
	const safeName = agentName.replace(/[^\w.-]+/g, "_");
	const filePath = path.join(tmpDir, `prompt-${safeName}.md`);
	await withFileMutationQueue(filePath, async () => {
		await fs.promises.writeFile(filePath, prompt, { encoding: "utf-8", mode: 0o600 });
	});
	return { dir: tmpDir, filePath };
}

function getPiInvocation(args: string[]): { command: string; args: string[] } {
	const currentScript = process.argv[1];
	const isBunVirtualScript = currentScript?.startsWith("/$bunfs/root/");
	if (currentScript && !isBunVirtualScript && fs.existsSync(currentScript)) {
		return { command: process.execPath, args: [currentScript, ...args] };
	}

	const execName = path.basename(process.execPath).toLowerCase();
	const isGenericRuntime = /^(node|bun)(\.exe)?$/.test(execName);
	if (!isGenericRuntime) {
		return { command: process.execPath, args };
	}

	return { command: "pi", args };
}

type OnUpdateCallback = (partial: AgentToolResult<SubagentDetails>) => void;

// How one child is launched: its model (or none, to use the agent file's or the parent's), the
// parent's thinking level for an inherited model, and the session id and name it is saved under.
interface Launch {
	seat?: Seat;
	fallback: boolean;
	parent?: Seat;
	thinkingLevel?: ThinkingLevel;
	sessionId: string;
	name: string;
}

async function runSingleAgent(
	defaultCwd: string,
	launch: Launch,
	agents: AgentConfig[],
	agentName: string,
	task: string,
	cwd: string | undefined,
	signal: AbortSignal | undefined,
	onUpdate: OnUpdateCallback | undefined,
	makeDetails: (results: SingleResult[]) => SubagentDetails,
): Promise<SingleResult> {
	const agent = agents.find((a) => a.name === agentName);

	if (!agent) {
		const available = agents.map((a) => `"${a.name}"`).join(", ") || "none";
		return {
			agent: agentName,
			task,
			exitCode: 1,
			messages: [],
			stderr: `Unknown agent: "${agentName}". Available agents: ${available}.`,
		};
	}

	const args: string[] = ["--mode", "json", "-p", "--session-dir", CHILD_SESSIONS, "--session-id", launch.sessionId, "--name", launch.name];
	const seat = launch.seat ?? (launch.fallback || !agent.model ? launch.parent : undefined);
	let model: string | undefined;
	if (seat) {
		args.push("--provider", seat.provider, "--model", seat.model);
		model = `${seat.provider}/${seat.model}`;
	} else if (agent.model) {
		args.push("--model", agent.model);
		model = agent.model;
	}
	const parentModel = seat && launch.parent && seat.provider === launch.parent.provider && seat.model === launch.parent.model;
	if (parentModel && launch.thinkingLevel) args.push("--thinking", launch.thinkingLevel);
	if (agent.tools && agent.tools.length > 0) args.push("--tools", agent.tools.join(","));

	let tmpPromptDir: string | null = null;
	let tmpPromptPath: string | null = null;

	const currentResult: SingleResult = {
		agent: agentName,
		task,
		exitCode: 0,
		messages: [],
		stderr: "",
		model,
	};

	const emitUpdate = () => {
		if (onUpdate) {
			onUpdate({
				content: [{ type: "text", text: getFinalOutput(currentResult.messages) || "(running...)" }],
				details: makeDetails([currentResult]),
			});
		}
	};

	try {
		const tmp = await writePromptToTempFile(agent.name, buildChildPrompt(agent.systemPrompt));
		tmpPromptDir = tmp.dir;
		tmpPromptPath = tmp.filePath;
		args.push("--append-system-prompt", tmpPromptPath);

		args.push(`Task: ${task}`);
		let wasAborted = false;

		const exitCode = await new Promise<number>((resolve) => {
			const invocation = getPiInvocation(args);
			const proc = spawn(invocation.command, invocation.args, {
				cwd: cwd ?? defaultCwd,
				env: childEnv(path.join(tmpPromptDir as string, "gh")),
				shell: false,
				stdio: ["ignore", "pipe", "pipe"],
			});
			let buffer = "";

			const processLine = (line: string) => {
				if (!line.trim()) return;
				let event: any;
				try {
					event = JSON.parse(line);
				} catch {
					return;
				}

				if (event.type === "message_end" && event.message) {
					const msg = event.message as Message;
					currentResult.messages.push(msg);

					if (msg.role === "assistant") {
						if (!currentResult.model && msg.model) currentResult.model = msg.model;
						if (msg.stopReason) currentResult.stopReason = msg.stopReason;
						if (msg.errorMessage) currentResult.errorMessage = msg.errorMessage;
					}
					emitUpdate();
				}

				if (event.type === "tool_result_end" && event.message) {
					currentResult.messages.push(event.message as Message);
					emitUpdate();
				}
			};

			proc.stdout.on("data", (data) => {
				buffer += data.toString();
				const lines = buffer.split("\n");
				buffer = lines.pop() || "";
				for (const line of lines) processLine(line);
			});

			proc.stderr.on("data", (data) => {
				currentResult.stderr += data.toString();
			});

			proc.on("close", (code) => {
				if (buffer.trim()) processLine(buffer);
				resolve(code ?? 0);
			});

			proc.on("error", () => {
				resolve(1);
			});

			if (signal) {
				const killProc = () => {
					wasAborted = true;
					proc.kill("SIGTERM");
					setTimeout(() => {
						if (!proc.killed) proc.kill("SIGKILL");
					}, 5000);
				};
				if (signal.aborted) killProc();
				else signal.addEventListener("abort", killProc, { once: true });
			}
		});

		currentResult.exitCode = exitCode;
		if (wasAborted) throw new Error("Subagent was aborted");
		return currentResult;
	} finally {
		if (tmpPromptDir)
			try {
				fs.rmSync(tmpPromptDir, { recursive: true, force: true });
			} catch {
				/* ignore */
			}
	}
}

const SubagentParams = Type.Object({
	agent: Type.String({ description: "Name of the agent to invoke" }),
	task: Type.String({ description: "Task to delegate to the agent" }),
	cwd: Type.Optional(Type.String({ description: "Working directory for the agent process" })),
	variant: Type.Optional(
		Type.Integer({ minimum: 1, description: "Which seat of a role with several (designer: 1 or 2), from config/models.json" }),
	),
});

export default function (pi: ExtensionAPI) {
	if (SUBAGENT_DEPTH >= 1) return;
	pi.on("session_start", (event, ctx) => (event.reason === "startup" ? checkDependencies(pi, ctx) : undefined));
	pi.registerTool({
		name: "subagent",
		label: "Subagent",
		description: `Delegate a task to a specialized subagent with isolated context. Agents are in ${path.join(getAgentDir(), "agents")}.`,
		parameters: SubagentParams,

		async execute(toolCallId, params, signal, onUpdate, ctx) {
			const parent: Seat | undefined = ctx.model ? { provider: ctx.model.provider, model: ctx.model.id } : undefined;
			const agents = discoverAgents();
			const makeDetails = (results: SingleResult[]): SubagentDetails => ({ results });

			const { seat: configured, label, error: seatError } = seatFor(params.agent, params.variant);
			if (seatError) return { content: [{ type: "text", text: `subagent: ${seatError}` }], details: makeDetails([]), isError: true };
			let seat = configured;
			let warning = "";
			const sameAsParent = seat && parent && seat.provider === parent.provider && seat.model === parent.model;
			const problem = seat && !sameAsParent ? await seatProblem(ctx, seat) : undefined;
			if (seat && problem) {
				const to = parent ? `${parent.provider}/${parent.model}` : "pi's default model";
				warning = `subagent: WARNING ${label} cannot use ${seat.provider}/${seat.model} (${problem}); falling back to the parent model ${to}`;
				console.error(warning);
				seat = undefined;
			}
			const launch: Launch = {
				seat,
				fallback: Boolean(warning),
				parent,
				thinkingLevel: ctx.thinkingLevel,
				sessionId: `${ctx.sessionManager.getSessionId()}-${label.replace(":", "-")}-${toolCallId.replace(/[^\w]+/g, "").slice(-12)}`,
				name: `role:${label}${warning ? " fallback" : ""}`,
			};
			const result = await runSingleAgent(
				ctx.cwd,
				launch,
				agents,
				params.agent,
				params.task,
				params.cwd,
				signal,
				onUpdate,
				makeDetails,
			);
			const prefix = warning ? `${warning}\n\n` : "";
			if (isFailedResult(result)) {
				return {
					content: [{ type: "text", text: `${prefix}Agent ${result.stopReason || "failed"}: ${getResultOutput(result)}` }],
					details: makeDetails([result]),
					isError: true,
				};
			}
			return {
				content: [{ type: "text", text: prefix + (getFinalOutput(result.messages) || "(no output)") }],
				details: makeDetails([result]),
			};
		},
	});
}

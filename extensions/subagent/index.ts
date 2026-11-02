// Vendored from pi 1.4.0, @earendil-works/pi-coding-agent examples/extensions/subagent/index.ts.
// Copyright (c) 2025 Mario Zechner. MIT License, see LICENSE.
// zstack changes, and only these: (a) the onigiri policy (resolved by resolvePolicy below) is
// appended to the child's system prompt after a subagent framing line;
// (b) PI_SUBAGENT_DEPTH >= 1 skips registering the tool, and children get depth + 1; (c) children
// are told not to sleep or poll; (d) with no UI in an untrusted project, project agents are
// refused; (e) children get an environment without forge or git credentials; (f) a session_start
// check reports a missing policy, agent file or gh login, and offers the fix only with a UI. Upstream's parallel and
// chain modes and its custom renderers are removed; pi's default tool renderer shows the result.
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
import { StringEnum } from "@earendil-works/pi-ai";
import {
	CONFIG_DIR_NAME,
	type ExtensionAPI,
	type ExtensionContext,
	getAgentDir,
	hasTrustRequiringProjectResources,
	ProjectTrustStore,
	withFileMutationQueue,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { type AgentConfig, type AgentScope, discoverAgents } from "./agents.ts";

const PACKAGE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const PACKAGE_POLICY = path.join(PACKAGE_DIR, "policy.md");
const NO_POLLING_LINE =
	"Do not use `sleep` or repeated polling to wait for anything. Long waits are the tick's job: report what you are waiting on and stop.";
const POLICY_FRAMING =
	"You are a subagent, not a top-level session. Of the onigiri policy below, only its hold, patent-hold, scope and commit/PR-text rules apply to you. Its Merging, Checks and Work sections describe the top-level session and do not apply to you. Where your agent prompt limits your tools, that limit wins over the policy.";
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

// pi reports a folder with no protected resources as trusted, and `.pi/agents/` is not a protected
// resource. So project agents count as trusted only after a real decision: pi resolved trust over
// protected resources, or the user saved one for this folder.
function projectAgentsTrusted(cwd: string, piTrusted: boolean): boolean {
	if (hasTrustRequiringProjectResources(cwd)) return piTrusted;
	return new ProjectTrustStore(getAgentDir()).get(cwd) === true;
}

// The one place the lookup order lives. First hit wins: $ONIGIRI_POLICY when set, even if that file is
// missing, because falling back past a policy the user named could swap their holds for the defaults;
// then the user's copy in the agent dir, which bin/setup.sh writes; then the package's own policy.md.
function resolvePolicy(): { path: string; source: string } {
	if (process.env.ONIGIRI_POLICY) return { path: process.env.ONIGIRI_POLICY, source: "$ONIGIRI_POLICY" };
	const userCopy = path.join(getAgentDir(), "onigiri", "policy.md");
	if (fs.existsSync(userCopy)) return { path: userCopy, source: "agent dir" };
	return { path: PACKAGE_POLICY, source: "package default" };
}

// A missing policy fails open, so the reviewer still runs before setup, but loudly: one stderr line
// naming the path, and one line in the child's prompt.
function buildChildPrompt(agentPrompt: string): string {
	const parts = [agentPrompt.trim(), NO_POLLING_LINE].filter(Boolean);
	const policyPath = resolvePolicy().path;
	let policy = "";
	let problem = "";
	try {
		policy = fs.readFileSync(policyPath, "utf-8").trim();
		if (!policy) problem = "is empty";
	} catch (error) {
		problem = `could not be read (${(error as NodeJS.ErrnoException).code ?? String(error)})`;
	}
	if (problem) {
		console.error(`subagent: onigiri policy ${policyPath} ${problem}; child runs without it`);
		parts.push(`The onigiri policy file ${policyPath} ${problem}, so no policy text is included here.`);
	} else {
		parts.push(POLICY_FRAMING, policy);
	}
	return `${parts.join("\n\n")}\n`;
}

interface Missing {
	message: string;
	remedy: string;
	fix?: { title: string; run: () => Promise<void> };
}

// What a fresh install can lack. Only the policy copy and the agent files have a fix to offer; gh is a warning.
async function findMissing(pi: ExtensionAPI, cwd: string): Promise<Missing[]> {
	const missing: Missing[] = [];
	const policy = resolvePolicy();
	const setup = path.join(PACKAGE_DIR, "bin", "setup.sh");
	if (!fs.existsSync(policy.path)) {
		missing.push({ message: `onigiri policy ${policy.path} (${policy.source}) does not exist`, remedy: "point $ONIGIRI_POLICY at a policy file, or unset it" });
	} else if (policy.source === "package default") {
		const dest = path.join(getAgentDir(), "onigiri", "policy.md");
		missing.push({
			message: `no editable policy at ${dest}, using the package default`,
			remedy: `bash ${setup}`,
			fix: { title: `Copy the default policy to ${dest}?`, run: async () => {
				fs.mkdirSync(path.dirname(dest), { recursive: true });
				fs.copyFileSync(PACKAGE_POLICY, dest, fs.constants.COPYFILE_EXCL);
			} },
		});
	}
	const agentsDir = path.join(getAgentDir(), "agents");
	const absent = fs.readdirSync(path.join(PACKAGE_DIR, "agents")).filter((f) => f.endsWith(".md") && !fs.existsSync(path.join(agentsDir, f)));
	if (absent.length > 0) {
		const script = path.join(PACKAGE_DIR, "bin", "install-agents.sh");
		missing.push({
			message: `agent files missing from ${agentsDir}: ${absent.join(", ")}`,
			remedy: `bash ${script}`,
			fix: { title: `Run bin/install-agents.sh to copy ${absent.join(", ")}?`, run: async () => {
				const r = await pi.exec("bash", [script], { timeout: 10000 });
				if (r.code !== 0) throw new Error(r.stderr || `exit ${r.code}`);
			} },
		});
	}
	// gh matters only for a repo hosted on GitHub. pi.exec runs gh in this process's environment; nothing reaches a child.
	const remotes = await pi.exec("git", ["-C", cwd, "remote", "-v"], { timeout: 5000 }).catch(() => undefined);
	if (remotes?.code === 0 && remotes.stdout.includes("github.com")) {
		const gh = await pi.exec("gh", ["auth", "status"], { timeout: 10000 }).catch(() => undefined);
		if (!gh || gh.code !== 0) missing.push({ message: "gh is not installed or not logged in; PR steps will fail", remedy: "gh auth login" });
	}
	return missing;
}

async function checkDependencies(pi: ExtensionAPI, ctx: ExtensionContext): Promise<void> {
	const policy = resolvePolicy();
	const say = (line: string) => (ctx.hasUI ? ctx.ui.notify(line, "warning") : console.error(line));
	if (policy.source !== "package default") say(`zstack: onigiri policy ${policy.path} (${policy.source})`);
	for (const m of await findMissing(pi, ctx.cwd)) {
		// No UI means nobody can answer, so never prompt and never block: print the remedy and go on.
		if (!ctx.hasUI || !m.fix) {
			say(`zstack: ${m.message}. Fix: ${m.remedy}`);
			continue;
		}
		if (!(await ctx.ui.confirm(m.fix.title, `${m.message}.\nSame as running: ${m.remedy}`))) continue;
		try {
			await m.fix.run();
			ctx.ui.notify(`zstack: fixed: ${m.message}`, "info");
		} catch (error) {
			ctx.ui.notify(`zstack: fix failed (${String(error)}). Run: ${m.remedy}`, "error");
		}
	}
}

interface SingleResult {
	agent: string;
	agentSource: "user" | "project" | "unknown";
	task: string;
	exitCode: number;
	messages: Message[];
	stderr: string;
	model?: string;
	stopReason?: string;
	errorMessage?: string;
}

interface SubagentDetails {
	agentScope: AgentScope;
	projectAgentsDir: string | null;
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

interface DispatchDefaults {
	model?: string;
	thinkingLevel?: ThinkingLevel;
}

async function runSingleAgent(
	defaultCwd: string,
	dispatchDefaults: DispatchDefaults,
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
			agentSource: "unknown",
			task,
			exitCode: 1,
			messages: [],
			stderr: `Unknown agent: "${agentName}". Available agents: ${available}.`,
		};
	}

	const args: string[] = ["--mode", "json", "-p", "--no-session"];
	const inheritsDispatchConfig = !agent.model;
	const model = agent.model ?? dispatchDefaults.model;
	if (model) args.push("--model", model);
	if (inheritsDispatchConfig && dispatchDefaults.thinkingLevel) {
		args.push("--thinking", dispatchDefaults.thinkingLevel);
	}
	if (agent.tools && agent.tools.length > 0) args.push("--tools", agent.tools.join(","));

	let tmpPromptDir: string | null = null;
	let tmpPromptPath: string | null = null;

	const currentResult: SingleResult = {
		agent: agentName,
		agentSource: agent.source,
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

const AgentScopeSchema = StringEnum(["user", "project", "both"] as const, {
	description: 'Which agent directories to use. Default: "user". Use "both" to include project-local agents.',
	default: "user",
});

const SubagentParams = Type.Object({
	agent: Type.String({ description: "Name of the agent to invoke" }),
	task: Type.String({ description: "Task to delegate to the agent" }),
	agentScope: Type.Optional(AgentScopeSchema),
	confirmProjectAgents: Type.Optional(
		Type.Boolean({ description: "Prompt before running project-local agents. Default: true.", default: true }),
	),
	cwd: Type.Optional(Type.String({ description: "Working directory for the agent process" })),
});

export default function (pi: ExtensionAPI) {
	if (SUBAGENT_DEPTH >= 1) return;
	pi.on("session_start", (_event, ctx) => checkDependencies(pi, ctx));
	pi.registerTool({
		name: "subagent",
		label: "Subagent",
		description: [
			"Delegate a task to a specialized subagent with isolated context.",
			`Default agent scope is "user" (from ${path.join(getAgentDir(), "agents")}).`,
			`To enable project-local agents in ${CONFIG_DIR_NAME}/agents, set agentScope: "both" (or "project").`,
		].join(" "),
		parameters: SubagentParams,

		async execute(_toolCallId, params, signal, onUpdate, ctx) {
			const agentScope: AgentScope = params.agentScope ?? "user";
			const dispatchDefaults: DispatchDefaults = {
				model: ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : undefined,
				thinkingLevel: ctx.thinkingLevel,
			};
			const discovery = discoverAgents(ctx.cwd, agentScope);
			const agents = discovery.agents;
			const confirmProjectAgents = params.confirmProjectAgents ?? true;

			const makeDetails = (results: SingleResult[]): SubagentDetails => ({
				agentScope,
				projectAgentsDir: discovery.projectAgentsDir,
				results,
			});

			const projectTrusted = projectAgentsTrusted(ctx.cwd, ctx.isProjectTrusted());
			if ((agentScope === "project" || agentScope === "both") && !ctx.hasUI && !projectTrusted) {
				return {
					content: [
						{
							type: "text",
							text: "Refused: project agents need a trusted project or an interactive session. With no UI there is nobody to confirm them. Use agentScope \"user\".",
						},
					],
					details: makeDetails([]),
					isError: true,
				};
			}

			if (
				(agentScope === "project" || agentScope === "both") &&
				confirmProjectAgents &&
				ctx.hasUI &&
				!projectTrusted
			) {
				const requested = agents.find((a) => a.name === params.agent);
				if (requested?.source === "project") {
					const dir = discovery.projectAgentsDir ?? "(unknown)";
					const ok = await ctx.ui.confirm(
						"Run project-local agents?",
						`Agents: ${requested.name}\nSource: ${dir}\n\nProject agents are repo-controlled. Only continue for trusted repositories.`,
					);
					if (!ok)
						return {
							content: [{ type: "text", text: "Canceled: project-local agents not approved." }],
							details: makeDetails([]),
						};
				}
			}

			const result = await runSingleAgent(
				ctx.cwd,
				dispatchDefaults,
				agents,
				params.agent,
				params.task,
				params.cwd,
				signal,
				onUpdate,
				makeDetails,
			);
			if (isFailedResult(result)) {
				return {
					content: [{ type: "text", text: `Agent ${result.stopReason || "failed"}: ${getResultOutput(result)}` }],
					details: makeDetails([result]),
					isError: true,
				};
			}
			return {
				content: [{ type: "text", text: getFinalOutput(result.messages) || "(no output)" }],
				details: makeDetails([result]),
			};
		},
	});
}

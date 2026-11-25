import { PARENT_THREAD_VAR, THREAD_VAR } from "../../hooks/rules.ts";

const CREDENTIAL_ENV = /TOKEN|SECRET|PASSW|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTH|ASKPASS/i;
const MODEL_PROVIDER_ENV =
	/^(ANTHROPIC|CLAUDE_CODE|OPENAI|AZURE_OPENAI|AI_GATEWAY|GEMINI|GOOGLE|GCLOUD|AWS|MISTRAL|GROQ|CEREBRAS|XAI|OPENROUTER|DEEPSEEK|MOONSHOT|KIMI|MINIMAX|ZAI|HF|FIREWORKS|TOGETHER|BASETEN|OPENCODE|META|CLOUDFLARE|XIAOMI|QWEN|ANT_LING|NVIDIA|RADIUS|TYPESAFE)_/;

export function childEnv(parentEnv: NodeJS.ProcessEnv, depth: number, ghConfigDir: string, sessionId: string): NodeJS.ProcessEnv {
	const env: NodeJS.ProcessEnv = {};
	for (const [key, value] of Object.entries(parentEnv)) {
		if (CREDENTIAL_ENV.test(key) && !MODEL_PROVIDER_ENV.test(key)) continue;
		env[key] = value;
	}
	delete env[PARENT_THREAD_VAR];
	const parent = parentEnv[THREAD_VAR];
	if (parent) env[PARENT_THREAD_VAR] = parent;
	env[THREAD_VAR] = `subagent:${sessionId}`;
	return { ...env, GH_CONFIG_DIR: ghConfigDir, GIT_TERMINAL_PROMPT: "0", PI_SUBAGENT_DEPTH: String(depth + 1) };
}

from utils.ai_executors.claude_code import ClaudeCodeExecutor
from utils.ai_executors.codex_cli import CodexCliExecutor
from utils.ai_executors.opencode_cli import OpenCodeCliExecutor


DEFAULT_EXECUTOR = "claude"
SUPPORTED_EXECUTORS = {"claude", "codex", "deepseek", "opencode"}

CLI_EXECUTORS = {
    "claude": ClaudeCodeExecutor,
    "codex": CodexCliExecutor,
    "opencode": OpenCodeCliExecutor,
}


def executor_name_from_env(environ):
    name = environ.get("AI_EXECUTOR", DEFAULT_EXECUTOR).strip().lower()
    if name not in SUPPORTED_EXECUTORS:
        allowed = ", ".join(sorted(SUPPORTED_EXECUTORS))
        raise RuntimeError(f"Unsupported AI_EXECUTOR {name!r}; expected one of: {allowed}.")
    return name


def create_cli_executor(name, environ=None):
    try:
        executor_type = CLI_EXECUTORS[name]
    except KeyError as exc:
        allowed = ", ".join(sorted(CLI_EXECUTORS))
        raise RuntimeError(
            f"AI_EXECUTOR {name!r} is not a CLI executor; expected one of: {allowed}."
        ) from exc
    return executor_type(environ=environ)


__all__ = [
    "ClaudeCodeExecutor",
    "CodexCliExecutor",
    "OpenCodeCliExecutor",
    "CLI_EXECUTORS",
    "DEFAULT_EXECUTOR",
    "SUPPORTED_EXECUTORS",
    "create_cli_executor",
    "executor_name_from_env",
]

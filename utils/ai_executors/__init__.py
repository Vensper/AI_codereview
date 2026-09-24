from utils.ai_executors.claude_code import ClaudeCodeExecutor


DEFAULT_EXECUTOR = "claude"
SUPPORTED_EXECUTORS = {"claude", "deepseek"}


def executor_name_from_env(environ):
    name = environ.get("AI_EXECUTOR", DEFAULT_EXECUTOR).strip().lower()
    if name not in SUPPORTED_EXECUTORS:
        allowed = ", ".join(sorted(SUPPORTED_EXECUTORS))
        raise RuntimeError(f"Unsupported AI_EXECUTOR {name!r}; expected one of: {allowed}.")
    return name


__all__ = [
    "ClaudeCodeExecutor",
    "DEFAULT_EXECUTOR",
    "SUPPORTED_EXECUTORS",
    "executor_name_from_env",
]

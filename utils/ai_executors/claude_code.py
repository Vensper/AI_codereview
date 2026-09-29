import json
import time

from utils.ai_executors.base import (
    CliExecutorBase,
    isolated_child_environment,
    run_process,
)


class _ClaudeStreamObserver:
    def __init__(self, budget):
        self.budget = budget
        self.seen_messages = set()
        self.seen_tool_calls = set()
        self.text_parts = []
        self.result = ""

    def __call__(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return

        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message") or {}
            message_id = message.get("id")
            identity = message_id or f"event-{len(self.seen_messages)}"
            if identity not in self.seen_messages:
                self.seen_messages.add(identity)
                self.budget.consume_round()
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    identity = block.get("id") or json.dumps(
                        block, ensure_ascii=False, sort_keys=True
                    )
                    if identity not in self.seen_tool_calls:
                        self.seen_tool_calls.add(identity)
                        self.budget.consume_tool_calls()
                elif block.get("type") == "text" and isinstance(
                    block.get("text"), str
                ):
                    self.text_parts.append(block["text"])
        elif event_type == "tool_use":
            identity = event.get("id") or json.dumps(
                event, ensure_ascii=False, sort_keys=True
            )
            if identity not in self.seen_tool_calls:
                self.seen_tool_calls.add(identity)
                self.budget.consume_tool_calls()
        elif event_type == "result":
            result = event.get("result")
            if isinstance(result, str):
                self.result = result

    def investigation_text(self):
        return (self.result or "\n".join(self.text_parts)).strip()


class ClaudeCodeExecutor(CliExecutorBase):
    name = "claude"
    display_name = "Claude Code"
    command_environment = "CLAUDE_CODE_COMMAND"
    default_command = "claude"

    def __init__(self, environ=None, command=None):
        super().__init__(environ=environ, command=command)

    def _environment(self):
        api_key = self.environ.get("ANTHROPIC_API_KEY", "").strip()
        auth_token = self.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        generic_key = self.environ.get("AI_API_KEY", "").strip()
        if not api_key and not auth_token and generic_key:
            auth_style = self.environ.get("AI_AUTH_STYLE", "api_key").strip().lower()
            if auth_style == "api_key":
                api_key = generic_key
            elif auth_style == "bearer":
                auth_token = generic_key
            else:
                raise RuntimeError("AI_AUTH_STYLE must be 'api_key' or 'bearer'.")
        if not api_key and not auth_token:
            raise RuntimeError(
                "Claude Code requires AI_API_KEY, ANTHROPIC_AUTH_TOKEN, "
                "or ANTHROPIC_API_KEY."
            )

        extra = {
            "ANTHROPIC_API_KEY": api_key or None,
            "ANTHROPIC_AUTH_TOKEN": auth_token or None,
            "ANTHROPIC_BASE_URL": (
                self.environ.get("AI_BASE_URL")
                or self.environ.get("ANTHROPIC_BASE_URL")
                or None
            ),
            "ANTHROPIC_CUSTOM_HEADERS": self.environ.get(
                "ANTHROPIC_CUSTOM_HEADERS"
            ),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        return isolated_child_environment(self.environ, extra), [api_key, auth_token]

    def _command(self, *, tools_enabled, output_format, schema=None):
        command = [
            self._executable(),
            "--print",
            "--bare",
            "--restricted",
            "--permission-mode",
            "dontAsk",
            "--tools",
        ]
        command.extend(["Read", "Glob", "Grep"] if tools_enabled else [""])
        command.extend(
            [
                "--disallowedTools",
                "Read(.git)",
                "Read(.git/**)",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--no-session-persistence",
                "--output-format",
                output_format,
            ]
        )
        if output_format == "stream-json":
            command.append("--verbose")
        if schema is not None:
            command.extend(
                [
                    "--json-schema",
                    json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
                ]
            )
        model = (
            self.environ.get("AI_MODEL_OVERRIDE")
            or self.environ.get("AI_MODEL")
            or ""
        ).strip()
        if model:
            command.extend(["--model", model])
        return command

    @staticmethod
    def _structured_text(stdout):
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Claude Code did not return JSON output.") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Claude Code returned an invalid JSON envelope.")
        if payload.get("is_error") is True:
            message = payload.get("result") or payload.get("error") or "unknown error"
            raise RuntimeError(f"Claude Code reported an error: {message}")

        structured = payload.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured, ensure_ascii=False)
        if isinstance(structured, str) and structured.strip():
            return structured.strip()

        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        raise RuntimeError("Claude Code output contains no structured result.")

    def investigate(self, prompt, workspace, timeout_seconds, budget):
        environment, secrets = self._environment()
        observer = _ClaudeStreamObserver(budget)
        started = time.monotonic()
        try:
            completed = run_process(
                self._command(tools_enabled=True, output_format="stream-json"),
                input_text=prompt,
                cwd=workspace,
                env=environment,
                timeout_seconds=timeout_seconds,
                stdout_line_callback=observer,
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start Claude Code: {exc}") from exc

        return self._complete_investigation(
            completed,
            observer.investigation_text(),
            secrets,
            budget,
            started,
        )

    def finalize(self, prompt, workspace, schema, timeout_seconds, budget, attempt=0):
        budget.consume_round()
        environment, secrets = self._environment()
        started = time.monotonic()
        try:
            completed = run_process(
                self._command(
                    tools_enabled=False,
                    output_format="json",
                    schema=schema,
                ),
                input_text=prompt,
                cwd=workspace,
                env=environment,
                timeout_seconds=timeout_seconds,
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start Claude Code: {exc}") from exc

        raw_output = self._complete_finalization(
            completed,
            completed.stdout.strip(),
            secrets,
            budget,
            started,
            attempt,
            require_text=False,
        )
        return self._structured_text(raw_output)

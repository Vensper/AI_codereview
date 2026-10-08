import json
import os
import shutil
import tempfile
import time

from utils.ai_executors.base import (
    ExecutionBudgetExceeded,
    ReviewDeadlineExceeded,
    isolated_child_environment,
    redact,
    run_process,
)
from utils.ai_executors.settings import cli_model_settings


_TOOL_ITEM_TYPES = {
    "command_execution",
    "file_change",
    "mcp_tool_call",
    "web_search",
}


class _CodexStreamObserver:
    def __init__(self, budget):
        self.budget = budget
        self.turn_seen = False
        self.seen_tool_calls = set()
        self.messages = {}

    def __call__(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return

        event_type = str(event.get("type") or "")
        if event_type == "turn.started" and not self.turn_seen:
            self.turn_seen = True
            self.budget.consume_round()

        item = event.get("item")
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "")
        identity = str(item.get("id") or json.dumps(item, sort_keys=True))
        if item_type in _TOOL_ITEM_TYPES and identity not in self.seen_tool_calls:
            self.seen_tool_calls.add(identity)
            self.budget.consume_tool_calls()
        if item_type == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                self.messages[identity] = text

    def text(self):
        return "\n".join(self.messages.values()).strip()


class CodexCliExecutor:
    name = "codex"

    def __init__(self, environ=None, command=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.command = command or self.environ.get("CODEX_CLI_COMMAND", "codex")
        self._trace = []

    def _settings(self):
        return cli_model_settings(
            self.environ,
            executor="Codex CLI",
            allowed_protocols={"openai_responses"},
        )

    def _executable(self):
        executable = shutil.which(self.command, path=self.environ.get("PATH"))
        if not executable:
            raise RuntimeError(f"Codex CLI executable was not found: {self.command!r}.")
        return executable

    @staticmethod
    def _provider_config(settings):
        values = [
            'model_provider="jenkins"',
            'model_providers.jenkins.name="Jenkins AI provider"',
            f"model_providers.jenkins.base_url={json.dumps(settings.base_url)}",
            'model_providers.jenkins.wire_api="responses"',
            "model_providers.jenkins.request_max_retries=2",
            "model_providers.jenkins.stream_max_retries=2",
            'approval_policy="never"',
            "check_for_update_on_startup=false",
            "analytics.enabled=false",
            "agents.enabled=false",
            'model_reasoning_summary="none"',
        ]
        if settings.auth_style == "bearer":
            values.append('model_providers.jenkins.env_key="AI_API_KEY"')
        else:
            values.append(
                'model_providers.jenkins.env_http_headers={"x-api-key"="AI_API_KEY"}'
            )
        return values

    def _command(self, settings, workspace, schema_path=None):
        command = [
            self._executable(),
            "exec",
            "--json",
            "--color",
            "never",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--cd",
            workspace,
            "--model",
            settings.model,
        ]
        for value in self._provider_config(settings):
            command.extend(["--config", value])
        if schema_path:
            command.extend(["--output-schema", schema_path])
        command.append("-")
        return command

    def _environment(self, settings, runtime_home):
        extra = {
            "AI_API_KEY": settings.api_key,
            "CODEX_HOME": runtime_home,
        }
        return isolated_child_environment(self.environ, extra), [settings.api_key]

    def _run(self, prompt, workspace, timeout_seconds, budget, schema=None):
        settings = self._settings()
        observer = _CodexStreamObserver(budget)
        with tempfile.TemporaryDirectory(prefix="ai-review-codex-") as runtime_home:
            schema_path = None
            if schema is not None:
                schema_path = os.path.join(runtime_home, "review.schema.json")
                with open(schema_path, "w", encoding="utf-8") as handle:
                    json.dump(schema, handle, ensure_ascii=False)
            environment, secrets = self._environment(settings, runtime_home)
            completed = run_process(
                self._command(settings, workspace, schema_path=schema_path),
                input_text=prompt,
                cwd=workspace,
                env=environment,
                timeout_seconds=timeout_seconds,
                stdout_line_callback=observer,
            )
        return completed, observer.text(), secrets

    def investigate(self, prompt, workspace, timeout_seconds, budget):
        started = time.monotonic()
        try:
            completed, text, secrets = self._run(
                prompt, workspace, timeout_seconds, budget
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start Codex CLI: {exc}") from exc

        if completed.timed_out:
            status = "investigation_timeout"
        elif isinstance(completed.monitor_error, ExecutionBudgetExceeded):
            status = "investigation_fuse"
        elif completed.monitor_error is not None:
            raise RuntimeError(f"Cannot monitor Codex CLI output: {completed.monitor_error}")
        elif completed.returncode != 0:
            detail = redact(completed.stderr.strip(), secrets)[-2000:]
            raise RuntimeError(
                f"Codex CLI investigation exited with code {completed.returncode}: "
                f"{detail or 'no stderr output'}"
            )
        else:
            status = "ok"

        self._trace.append(
            {
                "executor": self.name,
                "phase": "investigation",
                "status": status,
                "return_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                **budget.snapshot(),
            }
        )
        return text

    def finalize(self, prompt, workspace, schema, timeout_seconds, budget, attempt=0):
        started = time.monotonic()
        try:
            completed, text, secrets = self._run(
                prompt, workspace, timeout_seconds, budget, schema=schema
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start Codex CLI: {exc}") from exc

        status = "ok" if completed.returncode == 0 else "error"
        if completed.timed_out:
            status = "timeout"
        self._trace.append(
            {
                "executor": self.name,
                "phase": "finalization",
                "attempt": attempt + 1,
                "status": status,
                "return_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                **budget.snapshot(),
            }
        )
        if completed.timed_out:
            raise ReviewDeadlineExceeded(
                "Codex CLI finalization reached the AI review deadline."
            )
        if completed.monitor_error is not None:
            raise completed.monitor_error
        if completed.returncode != 0:
            detail = redact(completed.stderr.strip(), secrets)[-2000:]
            raise RuntimeError(
                f"Codex CLI finalization exited with code {completed.returncode}: "
                f"{detail or 'no stderr output'}"
            )
        if not text:
            raise ValueError("Codex CLI returned no structured output.")
        return text

    def get_trace(self):
        return list(self._trace)

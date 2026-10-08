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


class _OpenCodeStreamObserver:
    def __init__(self, budget):
        self.budget = budget
        self.seen_steps = set()
        self.seen_tool_calls = set()
        self.text_parts = {}
        self.errors = []

    def __call__(self, line):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return

        event_type = str(event.get("type") or "")
        if event_type == "error":
            message = self._error_message(event.get("error"))
            if message and message not in self.errors:
                self.errors.append(message)

        part = event.get("part")
        if not isinstance(part, dict):
            part = {}
        part_type = str(part.get("type") or "")
        identity = str(
            part.get("id")
            or event.get("id")
            or f"{event_type}-{len(self.seen_steps)}"
        )

        if event_type == "step_start" or part_type == "step-start":
            if identity not in self.seen_steps:
                self.seen_steps.add(identity)
                self.budget.consume_round()

        if part_type in {"tool", "tool-use", "tool_use"}:
            if identity not in self.seen_tool_calls:
                self.seen_tool_calls.add(identity)
                self.budget.consume_tool_calls()

        if event_type == "text" or part_type == "text":
            value = part.get("text")
            if not isinstance(value, str):
                value = event.get("text")
            if isinstance(value, str) and value.strip():
                self.text_parts[identity] = value

    def text(self):
        return "\n".join(self.text_parts.values()).strip()

    def error_text(self):
        return "\n".join(self.errors).strip()

    @staticmethod
    def _error_message(error):
        if isinstance(error, str):
            return error.strip()
        if not isinstance(error, dict):
            return ""

        name = error.get("name")
        data = error.get("data")
        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, str) or not message.strip():
            message = error.get("message")
        if isinstance(message, str) and message.strip():
            if isinstance(name, str) and name.strip():
                return f"{name.strip()}: {message.strip()}"
            return message.strip()
        if isinstance(name, str):
            return name.strip()
        return ""


class OpenCodeCliExecutor:
    name = "opencode"

    def __init__(self, environ=None, command=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.command = command or self.environ.get("OPENCODE_CLI_COMMAND", "opencode")
        self._trace = []
        self._temporary_cache = None

    def _settings(self):
        settings = cli_model_settings(
            self.environ,
            executor="OpenCode CLI",
            allowed_protocols={"openai_chat", "openai_responses"},
        )
        if settings.auth_style != "bearer":
            raise RuntimeError(
                "OpenCode CLI currently requires AI_AUTH_STYLE=bearer for "
                "OpenAI-compatible providers."
            )
        return settings

    def _executable(self):
        executable = shutil.which(self.command, path=self.environ.get("PATH"))
        if not executable:
            raise RuntimeError(
                f"OpenCode CLI executable was not found: {self.command!r}."
            )
        return executable

    @staticmethod
    def _config(settings, tools_enabled):
        package = (
            "@ai-sdk/openai"
            if settings.protocol == "openai_responses"
            else "@ai-sdk/openai-compatible"
        )
        permission = {
            "*": "deny",
            "read": "allow" if tools_enabled else "deny",
            "glob": "allow" if tools_enabled else "deny",
            "grep": "allow" if tools_enabled else "deny",
            "list": "deny",
            "edit": "deny",
            "bash": "deny",
            "task": "deny",
            "external_directory": "deny",
            "todowrite": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "lsp": "deny",
            "skill": "deny",
            "question": "deny",
            "doom_loop": "deny",
        }
        return {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "plugin": [],
            "mcp": {},
            "formatter": False,
            "lsp": False,
            "enabled_providers": ["jenkins"],
            "model": f"jenkins/{settings.model}",
            "provider": {
                "jenkins": {
                    "npm": package,
                    "name": "Jenkins AI provider",
                    "options": {
                        "baseURL": settings.base_url,
                        "apiKey": "{env:AI_API_KEY}",
                    },
                    "models": {settings.model: {"name": settings.model}},
                }
            },
            "permission": permission,
        }

    def _command(self, settings, workspace):
        return [
            self._executable(),
            "run",
            "--format",
            "json",
            "--pure",
            "--dir",
            workspace,
            "--model",
            f"jenkins/{settings.model}",
        ]

    def _environment(self, settings, runtime_home, tools_enabled):
        config_root = os.path.join(runtime_home, "config")
        data_root = os.path.join(runtime_home, "data")
        cache_root = self.environ.get("OPENCODE_CACHE_HOME", "").strip()
        if not cache_root:
            if self._temporary_cache is None:
                self._temporary_cache = tempfile.TemporaryDirectory(
                    prefix="ai-review-opencode-cache-"
                )
            cache_root = self._temporary_cache.name
        state_root = os.path.join(runtime_home, "state")
        temp_root = os.path.join(runtime_home, "tmp")
        extra = {
            "AI_API_KEY": settings.api_key,
            "XDG_CONFIG_HOME": config_root,
            "XDG_DATA_HOME": data_root,
            "XDG_CACHE_HOME": cache_root,
            "XDG_STATE_HOME": state_root,
            "TEMP": temp_root,
            "TMP": temp_root,
            "OPENCODE_CONFIG_DIR": os.path.join(config_root, "opencode"),
            "OPENCODE_CONFIG_CONTENT": json.dumps(
                self._config(settings, tools_enabled),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
            "OPENCODE_PURE": "1",
            "NPM_CONFIG_REGISTRY": self.environ.get("OPENCODE_NPM_REGISTRY")
            or None,
        }
        return isolated_child_environment(self.environ, extra), [settings.api_key]

    @staticmethod
    def _reject_executable_project_config(workspace):
        for name in ("opencode.json", "opencode.jsonc", ".opencode"):
            if os.path.lexists(os.path.join(workspace, name)):
                raise RuntimeError(
                    f"OpenCode executor refuses repository-controlled {name!r}; "
                    "remove it from the premerge root or use another executor."
                )

    def _run(self, prompt, workspace, timeout_seconds, budget, tools_enabled):
        self._reject_executable_project_config(workspace)
        settings = self._settings()
        observer = _OpenCodeStreamObserver(budget)
        with tempfile.TemporaryDirectory(prefix="ai-review-opencode-") as runtime_home:
            environment, secrets = self._environment(
                settings, runtime_home, tools_enabled
            )
            completed = run_process(
                self._command(settings, workspace),
                input_text=prompt,
                cwd=workspace,
                env=environment,
                timeout_seconds=timeout_seconds,
                stdout_line_callback=observer,
            )
        return completed, observer.text(), observer.error_text(), secrets

    @staticmethod
    def _failure_detail(completed, observed_error, secrets):
        raw_detail = observed_error or completed.stderr.strip()
        return redact(raw_detail, secrets)[-2000:]

    def investigate(self, prompt, workspace, timeout_seconds, budget):
        started = time.monotonic()
        try:
            completed, text, observed_error, secrets = self._run(
                prompt, workspace, timeout_seconds, budget, tools_enabled=True
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start OpenCode CLI: {exc}") from exc

        if completed.timed_out:
            status = "investigation_timeout"
        elif isinstance(completed.monitor_error, ExecutionBudgetExceeded):
            status = "investigation_fuse"
        elif completed.monitor_error is not None:
            raise RuntimeError(
                f"Cannot monitor OpenCode CLI output: {completed.monitor_error}"
            )
        elif completed.returncode != 0:
            detail = self._failure_detail(completed, observed_error, secrets)
            raise RuntimeError(
                f"OpenCode CLI investigation exited with code {completed.returncode}: "
                f"{detail or 'no error output'}"
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
        del schema  # OpenCode 1.18.31 has no native output-schema CLI option.
        started = time.monotonic()
        try:
            completed, text, observed_error, secrets = self._run(
                prompt, workspace, timeout_seconds, budget, tools_enabled=False
            )
        except OSError as exc:
            raise RuntimeError(f"Cannot start OpenCode CLI: {exc}") from exc

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
                "OpenCode CLI finalization reached the AI review deadline."
            )
        if completed.monitor_error is not None:
            raise completed.monitor_error
        if completed.returncode != 0:
            detail = self._failure_detail(completed, observed_error, secrets)
            raise RuntimeError(
                f"OpenCode CLI finalization exited with code {completed.returncode}: "
                f"{detail or 'no error output'}"
            )
        if not text:
            raise ValueError("OpenCode CLI returned no structured output.")
        return text

    def get_trace(self):
        return list(self._trace)

    def close(self):
        if self._temporary_cache is not None:
            self._temporary_cache.cleanup()
            self._temporary_cache = None

    def __del__(self):
        self.close()

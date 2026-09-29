import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass


_PASSTHROUGH_ENV = {
    "ALL_PROXY",
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LOCALAPPDATA",
    "NODE_EXTRA_CA_CERTS",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}

TOTAL_REVIEW_SECONDS = 600
INVESTIGATION_SECONDS = 480
FINALIZATION_SECONDS = 120
MAX_MODEL_ROUNDS = 64
MAX_TOOL_CALLS = 128


class ReviewDeadlineExceeded(RuntimeError):
    pass


class ExecutionBudgetExceeded(RuntimeError):
    pass


class ReviewDeadline:
    """One monotonic deadline shared by investigation, finalization and retries."""

    def __init__(
        self,
        total_seconds=TOTAL_REVIEW_SECONDS,
        investigation_seconds=INVESTIGATION_SECONDS,
        finalization_seconds=FINALIZATION_SECONDS,
        clock=time.monotonic,
    ):
        self._clock = clock
        self.started_at = clock()
        self.total_deadline = self.started_at + total_seconds
        self.investigation_deadline = min(
            self.total_deadline, self.started_at + investigation_seconds
        )
        self.finalization_seconds = finalization_seconds
        self.finalization_deadline = None

    def investigation_remaining(self):
        return max(
            0.0,
            min(self.investigation_deadline, self.total_deadline) - self._clock(),
        )

    def begin_finalization(self):
        if self.finalization_deadline is None:
            self.finalization_deadline = min(
                self.total_deadline, self._clock() + self.finalization_seconds
            )
        return self.finalization_remaining()

    def finalization_remaining(self):
        if self.finalization_deadline is None:
            raise RuntimeError("Finalization deadline has not started.")
        return max(
            0.0,
            min(self.finalization_deadline, self.total_deadline) - self._clock(),
        )

    def require_investigation_time(self):
        remaining = self.investigation_remaining()
        if remaining <= 0:
            raise ReviewDeadlineExceeded("AI investigation reached its 480-second limit.")
        return remaining

    def require_finalization_time(self):
        remaining = self.finalization_remaining()
        if remaining <= 0:
            raise ReviewDeadlineExceeded("AI review reached its 600-second deadline.")
        return remaining

    def sleep(self, seconds, phase):
        remaining = (
            self.investigation_remaining()
            if phase == "investigation"
            else self.finalization_remaining()
        )
        if remaining <= 0:
            raise ReviewDeadlineExceeded(f"AI {phase} deadline expired.")
        self._clock_sleep(min(seconds, remaining))
        if seconds >= remaining:
            raise ReviewDeadlineExceeded(f"AI {phase} deadline expired.")

    @staticmethod
    def _clock_sleep(seconds):
        time.sleep(seconds)


class ExecutionBudget:
    """High emergency fuse for observable model rounds and tool calls."""

    def __init__(self, max_rounds=MAX_MODEL_ROUNDS, max_tool_calls=MAX_TOOL_CALLS):
        self.max_rounds = max_rounds
        self.max_tool_calls = max_tool_calls
        self.model_rounds = 0
        self.tool_calls = 0

    def consume_round(self, count=1):
        if self.model_rounds + count > self.max_rounds:
            raise ExecutionBudgetExceeded(
                f"AI model-round fuse exceeded ({self.max_rounds})."
            )
        self.model_rounds += count

    def consume_tool_calls(self, count=1):
        if self.tool_calls + count > self.max_tool_calls:
            raise ExecutionBudgetExceeded(
                f"AI tool-call fuse exceeded ({self.max_tool_calls})."
            )
        self.tool_calls += count

    def snapshot(self):
        return {
            "model_rounds": self.model_rounds,
            "tool_calls": self.tool_calls,
            "max_model_rounds": self.max_rounds,
            "max_tool_calls": self.max_tool_calls,
        }


class CliExecutorBase:
    """Shared process-result handling for the supported review CLIs.

    Subclasses keep ownership of command construction, isolated environments,
    stream parsing and structured-output decoding.  This class only centralizes
    the lifecycle rules that must stay identical across executors.
    """

    name = ""
    display_name = "AI CLI"
    command_environment = ""
    default_command = ""

    def __init__(self, environ=None, command=None):
        self.environ = dict(os.environ if environ is None else environ)
        self.command = command or self.environ.get(
            self.command_environment, self.default_command
        )
        self._trace = []

    def _executable(self):
        executable = shutil.which(self.command, path=self.environ.get("PATH"))
        if not executable:
            raise RuntimeError(
                f"{self.display_name} executable was not found: {self.command!r}."
            )
        return executable

    def _record_trace(self, phase, status, completed, budget, started, attempt=None):
        entry = {
            "executor": self.name,
            "phase": phase,
        }
        if attempt is not None:
            entry["attempt"] = attempt + 1
        entry.update(
            {
                "status": status,
                "return_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                **budget.snapshot(),
            }
        )
        self._trace.append(entry)

    def _complete_investigation(
        self, completed, text, secrets, budget, started
    ):
        if completed.timed_out:
            status = "investigation_timeout"
        elif isinstance(completed.monitor_error, ExecutionBudgetExceeded):
            status = "investigation_fuse"
        elif completed.monitor_error is not None:
            raise RuntimeError(
                f"Cannot monitor {self.display_name} output: "
                f"{completed.monitor_error}"
            )
        elif completed.returncode != 0:
            detail = redact(completed.stderr.strip(), secrets)[-2000:]
            raise RuntimeError(
                f"{self.display_name} investigation exited with code "
                f"{completed.returncode}: {detail or 'no stderr output'}"
            )
        else:
            status = "ok"

        self._record_trace(
            "investigation", status, completed, budget, started
        )
        return text

    def _complete_finalization(
        self,
        completed,
        text,
        secrets,
        budget,
        started,
        attempt,
        *,
        require_text=True,
    ):
        status = "ok" if completed.returncode == 0 else "error"
        if completed.timed_out:
            status = "timeout"
        self._record_trace(
            "finalization",
            status,
            completed,
            budget,
            started,
            attempt=attempt,
        )
        if completed.timed_out:
            raise ReviewDeadlineExceeded(
                f"{self.display_name} finalization reached the AI review deadline."
            )
        if completed.monitor_error is not None:
            raise completed.monitor_error
        if completed.returncode != 0:
            detail = redact(completed.stderr.strip(), secrets)[-2000:]
            raise RuntimeError(
                f"{self.display_name} finalization exited with code "
                f"{completed.returncode}: {detail or 'no stderr output'}"
            )
        if require_text and not text:
            raise ValueError(f"{self.display_name} returned no structured output.")
        return text

    def get_trace(self):
        return list(self._trace)

    def close(self):
        """Release optional executor resources; most executors have none."""


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    monitor_error: Exception = None


def terminate_process_tree(process):
    """Terminate a CLI and all descendants, including npm/.cmd child processes."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=creationflags,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_process(
    command,
    *,
    input_text,
    cwd,
    env,
    timeout_seconds,
    stdout_line_callback=None,
):
    """Run a CLI with streamed monitoring and process-tree timeout cleanup."""
    if timeout_seconds <= 0:
        raise ReviewDeadlineExceeded("AI review deadline expired before CLI start.")
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if os.name == "nt"
        else 0
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )

    stdout_parts = []
    stderr_parts = []
    monitor_errors = []

    def read_stream(stream, parts, callback=None):
        try:
            for line in iter(stream.readline, ""):
                parts.append(line)
                if callback is not None and not monitor_errors:
                    try:
                        callback(line)
                    except Exception as exc:  # surfaced on the controlling thread
                        monitor_errors.append(exc)
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=read_stream,
        args=(process.stdout, stdout_parts, stdout_line_callback),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=read_stream,
        args=(process.stderr, stderr_parts),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        process.stdin.write(input_text)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    expires_at = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if monitor_errors:
            terminate_process_tree(process)
            break
        if time.monotonic() >= expires_at:
            timed_out = True
            terminate_process_tree(process)
            break
        time.sleep(0.05)

    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    return ProcessResult(
        returncode=process.returncode if process.returncode is not None else -1,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        timed_out=timed_out,
        monitor_error=monitor_errors[0] if monitor_errors else None,
    )


def isolated_child_environment(environ=None, extra=None):
    """Return a small environment suitable for an untrusted-code review CLI."""
    source = os.environ if environ is None else environ
    child = {
        name: source[name]
        for name in _PASSTHROUGH_ENV
        if name in source and source[name]
    }
    if extra:
        child.update({key: value for key, value in extra.items() if value is not None})
    return child


def redact(text, secrets):
    result = text or ""
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result

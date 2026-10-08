import os
import re
import sys
import urllib.parse
from dataclasses import dataclass

from utils.common import write_text


EXECUTOR_PROTOCOLS = {
    "claude": "anthropic",
    "codex": "openai_responses",
}
OPENCODE_PROTOCOLS = {"openai_chat", "openai_responses"}
PROTOCOL_URL_ENV = {
    "anthropic": "AI_GATEWAY_ANTHROPIC_URL",
    "openai_chat": "AI_GATEWAY_CHAT_URL",
    "openai_responses": "AI_GATEWAY_RESPONSES_URL",
}
DEFAULT_ROUTES = {
    "claude": {
        "protocol": "anthropic",
        "base_url": "https://api.deepseek.com/anthropic",
        "model": "deepseek-flash[1m]",
    },
    "codex": {
        "protocol": "openai_responses",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    },
    "opencode": {
        "protocol": "openai_chat",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash[1m]",
    },
}
DEFAULT_AUTH_STYLE = "bearer"
DEFAULT_CREDENTIAL_ID = "deepseek-api-key"
CREDENTIAL_ID_RE = re.compile(r"(?:deepseek-api-key|ai-model-[A-Za-z0-9._-]+)\Z")


@dataclass(frozen=True)
class GatewayRoute:
    executor: str
    protocol: str
    base_url: str
    model: str
    auth_style: str
    credential_id: str


def _required(environ, name):
    value = environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def _configured(environ, *names, default=""):
    for name in names:
        value = environ.get(name, "").strip()
        if value:
            return value
    return default


def _validate_model(model):
    if len(model) > 256:
        raise RuntimeError("AI_MODEL must not exceed 256 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in model):
        raise RuntimeError("AI_MODEL must not contain control characters.")
    return model


def _validate_auth_style(value, executor):
    auth_style = value.lower()
    if auth_style not in {"bearer", "api_key"}:
        raise RuntimeError("AI_AUTH_STYLE must be 'bearer' or 'api_key'.")
    if executor == "opencode" and auth_style != "bearer":
        raise RuntimeError("OpenCode currently requires AI_AUTH_STYLE=bearer.")
    return auth_style


def _validate_credential_id(value):
    if not CREDENTIAL_ID_RE.fullmatch(value):
        raise RuntimeError(
            "AI_CREDENTIAL_ID must be deepseek-api-key or start with ai-model-."
        )
    return value


def _validate_https_base_url(value, name):
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise RuntimeError(f"{name} must not contain whitespace or control characters.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError(f"{name} must be an HTTPS base URL.")
    if parsed.username or parsed.password:
        raise RuntimeError(f"{name} must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{name} must not contain a query string or fragment.")
    return value.rstrip("/")


def resolve_gateway_route(environ=None):
    environ = os.environ if environ is None else environ
    executor = _required(environ, "AI_EXECUTOR").lower()
    if executor not in {"claude", "codex", "opencode"}:
        raise RuntimeError(f"Unsupported AI_EXECUTOR: {executor!r}.")

    defaults = DEFAULT_ROUTES[executor]
    executor_prefix = f"AI_{executor.upper()}"

    if executor == "opencode":
        protocol = _configured(
            environ,
            "AI_OPENCODE_PROTOCOL",
            default=defaults["protocol"],
        ).lower()
        if protocol not in OPENCODE_PROTOCOLS:
            allowed = ", ".join(sorted(OPENCODE_PROTOCOLS))
            raise RuntimeError(
                f"Unsupported AI_OPENCODE_PROTOCOL: {protocol!r}; "
                f"expected one of: {allowed}."
            )
    else:
        protocol = EXECUTOR_PROTOCOLS[executor]

    url_env = PROTOCOL_URL_ENV[protocol]
    base_url = _validate_https_base_url(
        _configured(environ, url_env, default=defaults["base_url"]),
        url_env,
    )
    model = _validate_model(
        _configured(
            environ,
            f"{executor_prefix}_MODEL",
            "AI_MODEL",
            default=defaults["model"],
        )
    )
    auth_style = _validate_auth_style(
        _configured(
            environ,
            f"{executor_prefix}_AUTH_STYLE",
            "AI_AUTH_STYLE",
            default=DEFAULT_AUTH_STYLE,
        ),
        executor,
    )
    credential_id = _validate_credential_id(
        _configured(
            environ,
            f"{executor_prefix}_CREDENTIAL_ID",
            "AI_CREDENTIAL_ID",
            default=DEFAULT_CREDENTIAL_ID,
        )
    )
    return GatewayRoute(
        executor,
        protocol,
        base_url,
        model,
        auth_style,
        credential_id,
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m utils.resolve_ai_gateway OUTPUT_FILE")
    route = resolve_gateway_route()
    write_text(
        sys.argv[1],
        "\n".join(
            [
                route.protocol,
                route.base_url,
                route.model,
                route.auth_style,
                route.credential_id,
            ]
        )
        + "\n",
    )
    print(
        f"AI gateway route resolved: executor={route.executor}, "
        f"protocol={route.protocol}, model={route.model}."
    )


if __name__ == "__main__":
    main()

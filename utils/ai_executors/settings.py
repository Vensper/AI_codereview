from dataclasses import dataclass


@dataclass(frozen=True)
class CliModelSettings:
    api_key: str
    base_url: str
    model: str
    protocol: str
    auth_style: str


def cli_model_settings(environ, *, executor, allowed_protocols):
    api_key = environ.get("AI_API_KEY", "").strip()
    base_url = environ.get("AI_BASE_URL", "").strip()
    model = (
        environ.get("AI_MODEL_OVERRIDE") or environ.get("AI_MODEL") or ""
    ).strip()
    protocol = environ.get("AI_API_PROTOCOL", "").strip().lower()
    auth_style = environ.get("AI_AUTH_STYLE", "bearer").strip().lower()

    if not api_key:
        raise RuntimeError(f"{executor} requires AI_API_KEY.")
    if not base_url:
        raise RuntimeError(f"{executor} requires AI_BASE_URL.")
    if not model:
        raise RuntimeError(f"{executor} requires AI_MODEL_OVERRIDE or AI_MODEL.")
    if protocol not in allowed_protocols:
        allowed = ", ".join(sorted(allowed_protocols))
        raise RuntimeError(
            f"{executor} does not support AI_API_PROTOCOL {protocol!r}; "
            f"expected one of: {allowed}."
        )
    if auth_style not in {"bearer", "api_key"}:
        raise RuntimeError("AI_AUTH_STYLE must be 'api_key' or 'bearer'.")

    return CliModelSettings(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        protocol=protocol,
        auth_style=auth_style,
    )

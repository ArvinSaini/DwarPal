import secrets


def new_id(prefix: str) -> str:
    """Prefixed random id, e.g. ``cs_x8Jq2lP0Vt3a`` (12 url-safe characters)."""
    return f"{prefix}_{secrets.token_urlsafe(9)}"

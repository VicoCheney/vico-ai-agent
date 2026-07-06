"""Path safety helpers shared by file-oriented tools."""

from __future__ import annotations

from pathlib import Path

_SAFE_ENV_EXAMPLES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
}

_SENSITIVE_FILENAMES = {
    ".netrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

_SENSITIVE_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
}

SENSITIVE_RG_GLOBS = (
    "!.env",
    "!.env.*",
    "!*.key",
    "!*.pem",
    "!*.p12",
    "!*.pfx",
    "!id_rsa",
    "!id_dsa",
    "!id_ecdsa",
    "!id_ed25519",
)

SENSITIVE_GREP_EXCLUDES = (
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
)


def is_sensitive_path(path: Path) -> bool:
    """Return True for local files that commonly contain credentials."""
    name = path.name.lower()
    if name in _SAFE_ENV_EXAMPLES:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name in _SENSITIVE_FILENAMES:
        return True
    return path.suffix.lower() in _SENSITIVE_SUFFIXES

"""Settings from the environment, with a `.env` file as a fallback for local runs.

The `.env` file (repo root or backend/) is git-ignored. A variable already set in the
environment always wins over the file, and nothing here ever prints a value.
"""

import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ENV_FILES = (BACKEND.parent / ".env", BACKEND / ".env")


def load_env_files() -> list[str]:
    """Read KEY=VALUE lines into the environment where not already set. Returns the names it set."""
    loaded = []
    for path in ENV_FILES:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError(f"Could not read {path}: {exc}") from exc
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


def setting(*names: str) -> str | None:
    """The first of these variables that is set (several spellings of one key are accepted)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def anthropic_key_present() -> bool:
    return bool(setting("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))


def oxr_app_id() -> str | None:
    return setting("OPENEXCHANGERATES_APP_ID", "OPENEXCHANGERATES_API_KEY", "OXR_APP_ID")


load_env_files()

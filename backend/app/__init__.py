"""Vestly. Importing the package reads a local .env (git-ignored) into the environment, never overriding."""

from app import config as _config  # noqa: F401

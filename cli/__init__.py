"""the fleet cli: pi check, pi dev, pi sync. see cli/main.py."""

from __future__ import annotations

import os
import subprocess
from functools import cache
from pathlib import Path

from . import ui

ROOT = Path(__file__).resolve().parents[1]


class Fault(Exception):
    """a problem the person running pi can fix.

    printed as-is with no exception type in front, because the message is the
    whole point. anything else that escapes keeps its type, so it is obvious
    which of the two it was.
    """


@cache
def dotenv() -> dict[str, str]:
    """the repo root's .env, if there is one. see .env.example"""
    path = ROOT / ".env"
    if not path.is_file():
        return {}

    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        text = raw.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        out[key.strip()] = value.strip().strip("\"'")
    return out


def env(name: str, default: str = "") -> str:
    """a setting from the real environment first, then .env"""
    return os.environ.get(name) or dotenv().get(name) or default


def sh(*cmd: str, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> str:
    """run a local command. its output is a detail line, or the Fault if it fails."""
    proc = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        env={**os.environ, **extra_env} if extra_env else None,
        capture_output=True,
        text=True,
    )
    body = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise Fault(
            f"{' '.join(cmd)}\n{body}" if body else f"{' '.join(cmd)} exited {proc.returncode}"
        )
    ui.detail(body)
    return proc.stdout

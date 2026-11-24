"""pi dev: one app's ui and api, together, until ctrl-c."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time

from . import ROOT, Fault, sh, ui
from .manifest import App, app, apps


def taken(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def preflight(a: App) -> None:
    busy = [f"{kind} {p}" for kind, p in (("api", a.api), ("web", a.web)) if taken(p)]
    if busy:
        raise Fault(
            f"{' and '.join(busy)} already in use, probably a dev server that did not exit\n"
            f"find it with: ss -lntp | grep -E '{a.api}|{a.web}'"
        )


def relay(stream, tag: str, out) -> None:
    """prefix a child's output so two processes stay legible"""
    prefix = ui.paint(ui.DIM, f"{tag:<4}│ ")
    for raw in iter(stream.readline, b""):
        out.write(f"{prefix}{raw.decode(errors='replace')}")
        out.flush()


def spawn(cmd: list[str], cwd, tag: str, extra: dict[str, str]) -> subprocess.Popen:
    child = subprocess.Popen(
        cmd,
        cwd=cwd,
        env={**os.environ, **extra},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    threading.Thread(target=relay, args=(child.stdout, tag, sys.stdout), daemon=True).start()
    return child


def main(args) -> int:
    if not args.app:
        raise Fault("name an app: pi dev <app>\n" + ", ".join(a.name for a in apps()))
    a = app(args.app)

    ui.line(f"dev {a.name}, ui {a.web_url}, api {a.api_url}")
    if a.needs:
        ui.warn(f"needs {a.needs}")
    preflight(a)

    if not (ROOT / "node_modules").is_dir():
        with ui.step("pnpm install"):
            sh("pnpm", "install", "--frozen-lockfile", "--silent")

    # the api embeds web/dist, so it has to exist before `go run` compiles
    with ui.step("embed ui"):
        sh("pnpm", "run", "build", cwd=a.web_dir)

    ui.line(ui.paint(ui.GREEN, "ready, ctrl-c stops both"))

    env = {"RPI_WEB_PORT": str(a.web), "RPI_API_URL": a.api_url}
    children = [
        spawn(["pnpm", "run", "dev"], a.web_dir, "web", env),
        spawn(["go", "run", "."], a.server_dir, "api", a.env),
    ]

    code = 0
    try:
        while all(c.poll() is None for c in children):
            time.sleep(0.2)
        # one half dying on its own is a failure, not a clean stop
        code = next((c.returncode for c in children if c.returncode), 1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        ui.line(f"stopped {a.name}")
    return code

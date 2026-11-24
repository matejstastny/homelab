"""pi sync: build, push, install, then reconcile dns. everything, every time.

there is no partial sync on purpose. a host gets its binaries, its shell, its
bin scripts and its service files in one payload, and the fleet's resolvers are
reconciled against dns.toml afterwards, so "synced" means one thing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import ROOT, Fault, adguard, payload, sh, ui
from .manifest import (
    Credentials,
    Host,
    apps_on,
    credentials_for,
    host,
    resolvers,
    select,
    sshpass_available,
)
from .remote import Unreachable, require_reachable, run, sudo

# the pis are all 64-bit raspberry pis running alpine
TARGET = {"GOOS": "linux", "GOARCH": "arm64", "CGO_ENABLED": "0"}


def human(size: float) -> str:
    """matches internal/human.Bytes so build output and the logs agree"""
    if size <= 0:
        return "0 B"
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1000:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    return f"{size:.1f} TB"


def build(h: Host) -> None:
    """cross-compile this host's apps into build/, each embedding its own ui"""
    out = ROOT / "build"
    out.mkdir(parents=True, exist_ok=True)

    for a in apps_on(h.name):
        with ui.step(f"{h.name} build {a.name}") as s:
            sh("pnpm", "run", "build", cwd=a.web_dir)
            sh(
                "go",
                "build",
                "-trimpath",
                "-ldflags=-s -w",
                "-o",
                str(out / a.name),
                ".",
                cwd=a.server_dir,
                extra_env=TARGET,
            )
            for f in a.files:
                (out / f["as"]).write_bytes((ROOT / a.name / f["from"]).read_bytes())
            s.say(human(sum((out / n).stat().st_size for n in a.artefacts if (out / n).is_file())))


def push(h: Host, creds: Credentials, dry: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = payload.build(h, Path(tmp) / "payload")
        with ui.step(f"{h.name} stage") as s:
            s.say(f"{len(bundle.names)} files, {human(bundle.size)}")
            ui.detail("\n".join(f"~/{n}" for n in bundle.names))

        if dry:
            return

        require_reachable(creds)

        # the payload is complete, so the service directory is replaced
        # wholesale and a file deleted from hosts/<name>/ stops lingering
        prune = f'rm -rf "$HOME/scripts" "$HOME/{payload.SERVICES_HOME}/{h.name}"'
        with ui.step(f"{h.name} push") as s:
            res = run(creds, f'{prune} && tar -C "$HOME" -xf -', stdin=bundle.tar())
            if not res.ok:
                raise Fault(res.message)
            s.say("unpacked into ~")


def install(h: Host, creds: Credentials) -> None:
    """run the host's install script and relay its own output.

    a `!` line opens a note and owns every plain line after it, down to the
    next `▸` step or blank line, because that is where the install script
    explains what it could not do and what to do by hand instead.
    """
    target = f"~/.local/share/pi-services/{h.name}/install"
    with ui.step(f"{h.name} install") as s:
        res = sudo(creds, f"exec sh {target}")
        steps, block = 0, []

        def flush():
            if block:
                ui.note("\n".join(block))
                block.clear()

        for ln in res.out.splitlines():
            text = ln.strip()
            if not text:
                flush()
            elif text.startswith("▸"):
                flush()
                steps += 1
                ui.detail(text)
            elif text.startswith("!"):
                flush()
                block.append(text)
            elif block:
                block.append(text)
            else:
                ui.detail(text)
        flush()

        if not res.ok:
            raise Fault(res.message)
        s.say(f"{steps} steps")


def dns(dry: bool) -> list[str]:
    """reconcile every resolver against dns.toml. the names are fleet-wide."""
    try:
        adguard.password()
    except Fault as exc:
        ui.error(str(exc))
        return ["dns"]

    failed = []
    for name in resolvers():
        try:
            with ui.step(f"{name} dns") as s:
                client = adguard.client_for(credentials_for(name))
                plan = adguard.plan(client, name)
                for domain, answer, why in plan.add:
                    ui.detail(f"+ {domain:<24} {answer:<16} {why}")
                for domain, answer in plan.remove:
                    ui.detail(f"- {domain:<24} {answer}")
                if plan.upstream_changes:
                    was = ", ".join(plan.upstream_from) or "none"
                    ui.detail(f"~ upstream {was} -> {', '.join(plan.upstream_to)}")

                if plan.empty:
                    s.say(f"{len(plan.keep)} names already right")
                    continue

                changes = [f"+{len(plan.add)}", f"-{len(plan.remove)}"]
                if plan.upstream_changes:
                    changes.append("upstream")
                if not dry:
                    adguard.apply(client, plan)
                s.say(("would be " if dry else "") + f"{' '.join(changes)}, {len(plan.keep)} kept")
        except (Fault, LookupError, OSError) as exc:
            failed.append(name)
            ui.error(str(exc))
    return failed


def main(args) -> int:
    names = select(args.hosts)
    ui.line(f"sync {', '.join(names)}" + (" (dry run)" if args.dry_run else ""))
    if not sshpass_available():
        ui.warn("sshpass not found, only key-auth hosts will work")

    if not (ROOT / "node_modules").is_dir():
        with ui.step("pnpm install"):
            sh("pnpm", "install", "--frozen-lockfile", "--silent")

    failed: list[str] = []
    for name in names:
        h = host(name)
        try:
            creds = credentials_for(name)
            # a dry run does not cross-compile: it is there to say what would
            # be written, and five go builds is not that
            if h.installable and not args.dry_run:
                build(h)
            push(h, creds, args.dry_run)
            if h.installable and not args.dry_run:
                install(h, creds)
        except (Unreachable, Fault, LookupError, OSError) as exc:
            failed.append(name)
            ui.error(str(exc))

    failed += dns(args.dry_run)

    if failed:
        ui.failed(", ".join(sorted(set(failed))))
        return 1
    ui.done(f"{len(names)} host{'' if len(names) == 1 else 's'}, {ui.elapsed():.1f}s")
    return 0

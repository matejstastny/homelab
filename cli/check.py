"""pi check: the whole repository, one pass per language.

a failure says which half of the repo broke and nothing more, so the output of
a good run is a dozen lines you can take in at a glance.
"""

from __future__ import annotations

from . import Fault, sh, ui
from .manifest import apps


def tracked(*globs: str) -> list[str]:
    out = sh("git", "ls-files", *globs).split("\n")
    return [f for f in out if f]


def install() -> None:
    sh("pnpm", "install", "--frozen-lockfile", "--silent")


def web(fix: bool) -> None:
    with ui.step("pnpm install"):
        install()

    with ui.step("prettier"):
        sh("pnpm", "exec", "prettier", "--write" if fix else "--check", ".")

    for a in apps():
        with ui.step(f"{a.name} web") as s:
            sh("pnpm", "run", "typecheck", cwd=a.web_dir)
            sh("pnpm", "run", "build", cwd=a.web_dir)
            s.say("typechecked and built")


def embeds() -> None:
    """every api embeds its own web/dist, so go cannot even parse without it"""
    with ui.step("pnpm install"):
        install()
    for a in apps():
        with ui.step(f"{a.name} web") as s:
            sh("pnpm", "run", "build", cwd=a.web_dir)
            s.say("built")


def go(fix: bool) -> None:
    files = tracked("*.go")
    with ui.step("gofmt") as s:
        if files:
            unformatted = sh("gofmt", "-w" if fix else "-l", *files).strip()
            if unformatted and not fix:
                raise Fault(f"not gofmt-formatted:\n{unformatted}\nrun: pi check --fix")
        s.say(f"{len(files)} files")

    with ui.step("golangci-lint"):
        sh("golangci-lint", "run", "./...")

    with ui.step("go vet"):
        sh("go", "vet", "./...")

    with ui.step("go test"):
        sh("go", "test", "./...")


def shell(fix: bool) -> None:
    scripts = tracked("bin/*", "hosts/*/install", "*.initd", "*.confd")
    with ui.step("sh -n") as s:
        for f in scripts:
            sh("sh", "-n", f)
        s.say(f"{len(scripts)} scripts parse")


def python(fix: bool) -> None:
    with ui.step("ruff"):
        sh("ruff", "format", *([] if fix else ["--check"]), ".")
        sh("ruff", "check", *(["--fix"] if fix else []), ".")

    with ui.step("python test"):
        sh("python3", "-m", "unittest", "discover", "-s", "cli", "-t", ".", "-q")


# order matters: the apis embed web/dist, so the uis are built first
STAGES = {"web": web, "go": go, "shell": shell, "python": python}


def main(args) -> int:
    names = args.stages or list(STAGES)
    if unknown := [n for n in names if n not in STAGES]:
        raise Fault(f"unknown stage {', '.join(unknown)}, have: {', '.join(STAGES)}")

    ui.line(f"check {', '.join(names)}" + (" --fix" if args.fix else ""))
    if "go" in names and "web" not in names:
        embeds()

    for name in names:
        STAGES[name](args.fix)

    ui.done(f"{ui.steps()} checks, {ui.elapsed():.1f}s")
    return 0

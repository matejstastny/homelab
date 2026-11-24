"""the command surface: three verbs, nothing else.

pi check      everything in the repo
pi dev ytdl   one app's ui and api
pi sync       build, push, install, dns, for every host
"""

from __future__ import annotations

import argparse
import os
import sys

from . import Fault, check, dev, sync, ui

EPILOG = """examples:
  pi check              format, lint, typecheck, build and test the repo
  pi check --fix        write the formatting instead of only checking it
  pi check go           one stage: web, go, shell or python
  pi dev tasks          the weekly planner's ui and api, locally
  pi sync               every host and both resolvers
  pi sync io            one host, dns still reconciled
  pi sync -n            say what would change, write nothing
"""


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pi",
        description="the repo's one command",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-v", "--verbose", action="store_true", help="show every command's output")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    c = sub.add_parser("check", help="format, lint, typecheck, build and test everything")
    c.add_argument("stages", nargs="*", metavar="STAGE", help="web, go, shell or python")
    c.add_argument("--fix", action="store_true", help="write the formatting")
    c.set_defaults(fn=check.main)

    d = sub.add_parser("dev", help="run one app's ui and api locally")
    d.add_argument("app", nargs="?", metavar="APP")
    d.set_defaults(fn=dev.main)

    s = sub.add_parser("sync", help="build, push, install and reconcile dns")
    s.add_argument("hosts", nargs="*", metavar="HOST", help="host names (default: all)")
    s.add_argument("-n", "--dry-run", action="store_true", help="say what would change")
    s.set_defaults(fn=sync.main)

    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    ui.setup(args.verbose)

    if args.command is None:
        p.print_help()
        return 0

    try:
        return args.fn(args)
    except KeyboardInterrupt:
        ui.line("interrupted")
        return 130
    except BrokenPipeError:
        # `pi check | head` closes the pipe on us; exit the way every other
        # unix tool does instead of complaining about it
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141
    except (Fault, LookupError, ValueError, OSError) as exc:
        ui.error(str(exc))
        return 1

"""output: one line per event, a clock in front of it.

    [   0.000] check
    [   1.204] gofmt                312 files
    [   3.918] golangci-lint        ok
    [  41.882] done                 14 steps, 41.9s

the step being worked on redraws in place with its clock running, so a slow
one still looks alive. when it finishes the line freezes and the next one
starts. that is the whole visual language.
"""

from __future__ import annotations

import os
import sys
import threading
import time

RESET = "\033[0m"
DIM = "\033[38;2;120;120;140m"
GREEN = "\033[38;2;166;227;161m"
RED = "\033[38;2;243;139;168m"
AMBER = "\033[38;2;242;205;140m"

LABEL = 20

START = time.monotonic()
_lock = threading.Lock()
_tty = sys.stdout.isatty()
_colour = _tty and not os.environ.get("NO_COLOR")
_verbose = False
_count = 0


def setup(verbose: bool) -> None:
    global _verbose
    _verbose = verbose


def steps() -> int:
    """how many steps have finished, for the last line"""
    return _count


def paint(code: str, text: str) -> str:
    return f"{code}{text}{RESET}" if _colour else text


def elapsed() -> float:
    return time.monotonic() - START


def _stamp() -> str:
    return paint(DIM, f"[{elapsed():7.3f}]")


def _write(text: str, end: str) -> None:
    with _lock:
        sys.stdout.write(f"\r\033[K{text}{end}" if _tty else f"{text}{end}")
        sys.stdout.flush()


def line(text: str = "") -> None:
    """a finished line, stamped"""
    _write(f"{_stamp()} {text}" if text else "", "\n")


def detail(text: str) -> None:
    """extra lines under a step, only with -v"""
    if not _verbose:
        return
    for ln in text.rstrip().splitlines():
        if ln.strip():
            _write(paint(DIM, f"           {ln}"), "\n")


def _tag(text: str, code: str) -> str:
    """a coloured label padded to the label column, escapes not counted"""
    return paint(code, text) + " " * max(1, LABEL - len(text))


def note(text: str) -> None:
    """something on the box needs a human; install scripts print these as !

    it sits under the step that produced it, like a detail line, but it is
    always shown because nobody is going to re-run the sync with -v to find it.
    a note is often more than one line: a headline plus why it matters, so the
    whole block prints, not just the first line.
    """
    lines = [ln.strip() for ln in text.rstrip().splitlines() if ln.strip()]
    if not lines:
        return
    head, *rest = lines
    _write(f"           {paint(AMBER, '!')} {head.lstrip('! ')}", "\n")
    for ln in rest:
        _write(f"             {paint(DIM, ln.lstrip('! '))}", "\n")


def warn(text: str) -> None:
    line(paint(AMBER, text))


def error(text: str) -> None:
    head, *rest = text.splitlines() or [""]
    line(paint(RED, head))
    for ln in rest:
        _write(paint(DIM, f"           {ln}"), "\n")


def done(text: str) -> None:
    line(f"{'done':<{LABEL}} {paint(GREEN, text)}")


def failed(text: str) -> None:
    line(f"{_tag('failed', RED)} {text}")


class Step:
    """one unit of work: a live line while it runs, a frozen one after."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.result = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def say(self, text: str) -> None:
        """what the line ends with once the step is done"""
        self.result = text

    def _tick(self) -> None:
        while not self._stop.wait(0.08):
            _write(f"{_stamp()} {self.label:<{LABEL}} {paint(DIM, '···')}", "")

    def __enter__(self) -> Step:
        if _tty:
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, kind, value, tb) -> bool:
        global _count
        _count += 1
        self._stop.set()
        if self._thread:
            self._thread.join()
        if kind is None:
            line(f"{self.label:<{LABEL}} {paint(DIM, self.result or 'ok')}")
        else:
            line(f"{self.label:<{LABEL}} {paint(RED, 'failed')}")
        return False


def step(label: str) -> Step:
    return Step(label)

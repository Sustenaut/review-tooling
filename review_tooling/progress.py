"""A little to read while a long step runs, on stderr: a line when a step starts, and
its count every so often. REVIEW_TOOLING_QUIET=1 turns it off."""
from __future__ import annotations

import os
import sys
import time

EVERY = 15.0  # seconds between counts


def say(text: str) -> None:
    if not os.environ.get("REVIEW_TOOLING_QUIET"):
        print(text, file=sys.stderr, flush=True)


def elapsed(start: float) -> str:
    seconds = int(time.monotonic() - start)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


class Steps:
    """Says a step's start, then how far along it is whenever EVERY seconds have passed,
    and its end only if it said a count on the way."""

    def __init__(self, what: str, total: int):
        self.what, self.total, self.done = what, total, 0
        self.start = self.last = time.monotonic()
        self.counted = False
        say(f"{what}: {total}")

    def tick(self) -> None:
        self.done += 1
        now = time.monotonic()
        if self.done == self.total:
            if self.counted:
                say(f"  {self.done}/{self.total} in {elapsed(self.start)}")
        elif now - self.last >= EVERY:
            self.last, self.counted = now, True
            say(f"  {self.done}/{self.total}")

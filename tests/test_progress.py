"""What a long step says while it runs: little, on stderr."""
from __future__ import annotations

from review_tooling import progress


def clock(monkeypatch, *times):
    moments = iter(times)
    monkeypatch.setattr(progress.time, "monotonic", lambda: next(moments))


def test_a_quick_step_says_only_that_it_started(capsys, monkeypatch):
    clock(monkeypatch, 0, 1, 2)
    steps = progress.Steps("composing items", 2)
    steps.tick()
    steps.tick()
    captured = capsys.readouterr()
    assert captured.err == "composing items: 2\n" and captured.out == ""


def test_a_slow_step_counts_every_few_seconds_and_says_how_long_it_took(capsys, monkeypatch):
    clock(monkeypatch, 0, 1, 16, 17, 32, 75, 80)
    steps = progress.Steps("fetching objects", 5)
    for _ in range(5):
        steps.tick()
    assert capsys.readouterr().err.splitlines() == [
        "fetching objects: 5", "  2/5", "  4/5", "  5/5 in 1m 20s"]


def test_a_step_with_nothing_to_do_says_nothing(capsys):
    progress.Steps("composing items", 0)
    assert capsys.readouterr().err == ""


def test_quiet_says_nothing(capsys, monkeypatch):
    monkeypatch.setenv("REVIEW_TOOLING_QUIET", "1")
    clock(monkeypatch, 0, 20, 40, 60)
    steps = progress.Steps("fetching objects", 2)
    steps.tick()
    steps.tick()
    assert capsys.readouterr().err == ""

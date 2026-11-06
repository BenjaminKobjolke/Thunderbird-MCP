from __future__ import annotations

from types import SimpleNamespace

from tbmcp import thunderbird_process


def test_stop_asks_every_window_and_waits(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    monkeypatch.setattr(thunderbird_process, "open_windows", lambda: [(1, "Inbox"), (2, "Message")])
    asked = []
    monkeypatch.setattr(thunderbird_process, "_ask_to_close", asked.append)
    states = iter([True, True, False])
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: next(states))
    monkeypatch.setattr(thunderbird_process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_a, **_kw: 1 / 0)
    assert thunderbird_process._stop()
    assert asked == [1, 2]


def test_stop_times_out_without_force_killing(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    monkeypatch.setattr(thunderbird_process, "open_windows", lambda: [(1, "Inbox")])
    asked = []
    monkeypatch.setattr(thunderbird_process, "_ask_to_close", asked.append)
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: True)
    clock = iter([0, 61])
    monkeypatch.setattr(thunderbird_process.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(thunderbird_process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_a, **_kw: 1 / 0)
    assert not thunderbird_process._stop()
    assert asked == [1]


def test_stop_does_nothing_when_closed(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: False)
    monkeypatch.setattr(thunderbird_process, "open_windows", lambda: 1 / 0)
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_a, **_kw: 1 / 0)
    assert thunderbird_process._stop()


def test_stop_waits_for_windowless_exit(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    windows = iter([[(1, "Inbox")], []])
    monkeypatch.setattr(thunderbird_process, "open_windows", lambda: next(windows))
    asked = []
    monkeypatch.setattr(thunderbird_process, "_ask_to_close", asked.append)
    states = iter([True, True, True, False])
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: next(states))
    clock = iter([0, 61, 61, 70])
    monkeypatch.setattr(thunderbird_process.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(thunderbird_process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_a, **_kw: 1 / 0)

    assert thunderbird_process._stop()
    assert asked == [1]


def test_stop_gives_up_after_exit_grace(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    windows = iter([[(1, "Inbox")], []])
    monkeypatch.setattr(thunderbird_process, "open_windows", lambda: next(windows))
    asked = []
    monkeypatch.setattr(thunderbird_process, "_ask_to_close", asked.append)
    monkeypatch.setattr(thunderbird_process, "is_running", lambda: True)
    clock = iter([0, 61, 61, 107])
    monkeypatch.setattr(thunderbird_process.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(thunderbird_process.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_a, **_kw: 1 / 0)

    assert not thunderbird_process._stop()
    assert asked == [1]


def test_running_pids_ignores_background_tasks(monkeypatch):
    monkeypatch.setattr(thunderbird_process.sys, "platform", "win32")
    result = SimpleNamespace(
        stdout='"thunderbird.exe","10","Console","1","100 K"\n'
        '"thunderbird.exe","11","Console","1","100 K"\n'
    )
    monkeypatch.setattr(thunderbird_process.subprocess, "run", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(thunderbird_process, "_background_task_pids", lambda: {11})
    assert thunderbird_process.running_pids() == [10]

    monkeypatch.setattr(thunderbird_process, "_background_task_pids", set)
    assert thunderbird_process.running_pids() == [10, 11]

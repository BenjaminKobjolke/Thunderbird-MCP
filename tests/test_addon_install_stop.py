from __future__ import annotations

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

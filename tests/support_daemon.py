"""Shared daemon setup for lifecycle test modules."""

from __future__ import annotations

from tbmcp.daemon import Daemon
from tbmcp.profile import ThunderbirdProfile


def _daemon(path) -> Daemon:
    profile = ThunderbirdProfile(path=path, name="test", is_default=True, root=path)
    return Daemon(profile, idle_timeout=0)

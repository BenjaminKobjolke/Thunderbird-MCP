"""Shared values for registering Thunderbird MCP with desktop clients."""

from __future__ import annotations

from dataclasses import dataclass

SERVER_NAME = "thunderbird"

#: Order clients are processed and reported in.
CLIENTS: tuple[str, ...] = (
    "claude-code",
    "codex",
    "claude-desktop",
    "cursor",
    "vscode",
    "gemini",
    "zed",
)

#: Clients pass this environment to every stdio server they launch.
CHILD_ENV: dict[str, str] = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}

#: These write-capable tools always need an explicit Codex approval.
APPROVE_TOOLS: tuple[str, ...] = ("pref_set", "account_set_server", "mail_send", "mail_delete")

#: Thunderbird may still be starting when a new server receives its first request.
CODEX_STARTUP_TIMEOUT = 60

CODEX_TOOL_TIMEOUT = 120


class SetupError(Exception):
    """A config we could read but must not touch. Reported, then skipped."""


@dataclass
class Outcome:
    client: str
    status: str  # added | updated | unchanged | skipped | failed
    target: str = ""
    detail: str = ""
    block: str = ""
    planned: bool = False

    @property
    def label(self) -> str:
        if not self.planned:
            return self.status
        return {"added": "would add", "updated": "would update"}.get(self.status, self.status)


@dataclass
class Ctx:
    scope: str = "user"
    dry_run: bool = False
    print_only: bool = False

    @property
    def preview(self) -> bool:
        return self.dry_run or self.print_only

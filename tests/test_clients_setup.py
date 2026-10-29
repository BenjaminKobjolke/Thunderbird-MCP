from __future__ import annotations

from tbmcp import client_launcher, clients
from tbmcp.client_codex import _codex_block
from tbmcp.client_config_files import _apply_json
from tbmcp.client_json_writers import _cursor
from tbmcp.client_launcher import _entry_for
from tbmcp.client_model import CHILD_ENV, CLIENTS, SERVER_NAME, Ctx, Outcome


def test_client_model_keeps_order_env_and_preview_contract() -> None:
    assert CLIENTS == (
        "claude-code",
        "codex",
        "claude-desktop",
        "cursor",
        "vscode",
        "gemini",
        "zed",
    )
    assert CHILD_ENV == {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    assert SERVER_NAME == "thunderbird"
    assert Ctx(dry_run=True).preview is True
    assert Outcome("codex", "added", planned=True).label == "would add"


def test_json_writer_preview_does_not_write_and_keeps_server_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    entry = _entry_for("cursor", "python", ["-m", "tbmcp", "serve"])

    outcome = _cursor(entry, Ctx(dry_run=True))

    assert outcome.status == "added"
    assert outcome.planned is True
    assert '"thunderbird"' in outcome.block
    assert '"command": "python"' in outcome.block
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_json_writer_preserves_existing_configuration(tmp_path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers":{"other":{"command":"kept"}}}\n', encoding="utf-8")
    entry = _entry_for("cursor", "python", ["-m", "tbmcp", "serve"])

    outcome = _apply_json("cursor", path, "mcpServers", entry, Ctx())

    assert outcome.status == "added"
    text = path.read_text(encoding="utf-8")
    assert '"other"' in text
    assert '"thunderbird"' in text


def test_codex_block_keeps_timeouts_and_explicit_write_approvals() -> None:
    block = _codex_block(
        {
            "command": "python",
            "args": ["-m", "tbmcp", "serve"],
            "env": CHILD_ENV,
            "startup_timeout_sec": 60,
            "tool_timeout_sec": 120,
            "default_tools_approval_mode": "writes",
            "tools": {"mail_send": {"approval_mode": "approve"}},
        }
    )

    assert "startup_timeout_sec = 60" in block
    assert "tool_timeout_sec = 120" in block
    assert 'default_tools_approval_mode = "writes"' in block
    assert 'approval_mode = "approve"' in block


def test_setup_dry_run_keeps_client_report_and_does_not_write(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        client_launcher, "server_command", lambda _settings: ("python", ["-m", "tbmcp"])
    )

    assert clients.run_setup(["cursor"], dry_run=True) == 0

    report = capsys.readouterr().out
    assert "cursor" in report
    assert "would add" in report
    assert not (tmp_path / ".cursor" / "mcp.json").exists()

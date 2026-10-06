# Installing the Thunderbird add-on

The MCP server talks to Thunderbird through a small add-on (`tbmcp-bridge`). It is
built from `addon/` in this clone, so install it from here, not from another copy.

The commands below assume the clone's environment at `.venv` (see Development in the
README: `uv venv && uv pip install -e ".[dev]"`). Run them from the clone root.

## 1. Build the add-on file

```powershell
& .\.venv\Scripts\python.exe -m tbmcp install-addon --manual
```

It prints the path of the built package, e.g.
`C:\Users\<you>\AppData\Local\tbmcp\addon\tbmcp-bridge-1.3.1-5becf2b5a8a9.xpi`.
The suffix is a hash of the add-on contents, so the same name means an identical
add-on.

## 2. Install it in Thunderbird

1. Menu (☰) > **Add-ons and Themes**.
2. Gear icon > **Install Add-on From File…**
3. Pick the `.xpi` from step 1.
4. Accept the permission prompt, then restart Thunderbird.

The add-on is unsigned. Thunderbird release builds allow that
(`MOZ_REQUIRE_SIGNING=false`), so no extra setting is needed.

Installing over an existing version replaces it; no need to remove the old one first.

## 3. Check it

```powershell
& .\.venv\Scripts\python.exe -m tbmcp doctor
```

`doctor` reports the installed add-on version against the one in this clone and
warns if Thunderbird is running an older build, even if its version matches.

## When to reinstall

Only after something under `addon/` changes. Python-only changes (`src/tbmcp/`) take
effect with an editable install as soon as the MCP server restarts.
After any change under `addon/`, rerun `tbmcp install-addon`; `doctor` and `tb_status`
flag an installed build that differs from the current source.
`tools\install_addon.bat` runs `install-addon --yes` and `doctor` in one step from
any folder or as a Tickets Watcher command.

## Automatic install

Without `--manual`, `install-addon` installs the package itself and restarts
Thunderbird once (it asks first; `--yes` skips the prompt, `--no-restart` leaves
Thunderbird closed). Pass `--profile <name or directory>` for a non-default profile.
The batch file uses the default profile; run the command by hand for a non-default profile.

## Portable Thunderbird

The daemon finds the portable profile automatically while Thunderbird runs. To pin it
even when Thunderbird is closed, set `TBMCP_PROFILE=D:\Apps\ThunderbirdPortable\Data\profile`
in the MCP server configuration. `tbmcp doctor` shows "profile chosen by" and the
daemon's profile.

For automatic add-on installation, run `tbmcp install-addon --profile
D:\Apps\ThunderbirdPortable\Data\profile` with `TBMCP_THUNDERBIRD` set to the portable
`thunderbird.exe`; alternatively use `tbmcp install-addon --manual`.

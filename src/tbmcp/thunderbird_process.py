"""Find, start, and stop Thunderbird processes for add-on installation."""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
from typing import TYPE_CHECKING

from .errors import TransportError

if TYPE_CHECKING:
    from .profile import ThunderbirdProfile


def running_executable() -> pathlib.Path | None:
    """Find the executable named by a running Thunderbird command line."""
    for line in running_command_lines():
        if re.search(r"(?:^|\s)-contentproc(?:\s|$)", line, re.I):
            continue
        # ponytail: reads the leading command token; wrapped launchers need structured process metadata.
        match = re.match(
            r'^\s*(?:"([^"\r\n]*[/\\]thunderbird(?:\.exe)?)"|'
            r"(.+?[/\\]thunderbird(?:\.exe)?))(?=\s|$)",
            line,
            re.I,
        )
        if match:
            path = pathlib.Path(match.group(1) or match.group(2))
            if path.is_file():
                return path
    return None


def find_thunderbird() -> pathlib.Path | None:
    """Locate the Thunderbird binary for this platform."""
    override = os.environ.get("TBMCP_THUNDERBIRD")
    if override and pathlib.Path(override).exists():
        return pathlib.Path(override)

    running = running_executable()
    if running is not None:
        return running

    saved = saved_user_variable("TBMCP_THUNDERBIRD")
    if saved and pathlib.Path(saved).is_file():
        return pathlib.Path(saved)

    if sys.platform == "win32":
        candidates = [
            pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Mozilla Thunderbird"
            / "thunderbird.exe",
            pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Mozilla Thunderbird"
            / "thunderbird.exe",
            pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
            / "Mozilla Thunderbird"
            / "thunderbird.exe",
        ]
    elif sys.platform == "darwin":
        candidates = [
            pathlib.Path("/Applications/Thunderbird.app/Contents/MacOS/thunderbird"),
            pathlib.Path.home() / "Applications/Thunderbird.app/Contents/MacOS/thunderbird",
        ]
    else:
        candidates = [
            pathlib.Path("/usr/bin/thunderbird"),
            pathlib.Path("/usr/local/bin/thunderbird"),
            pathlib.Path("/snap/bin/thunderbird"),
        ]
    for path in candidates:
        if path and path.is_file():
            return path
    found = shutil.which("thunderbird")
    return pathlib.Path(found) if found else None


def running_pids() -> list[int]:
    """PIDs of running Thunderbird processes, best effort and dependency-free."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq thunderbird.exe", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            pids = []
            for line in out.splitlines():
                parts = [p.strip('" ') for p in line.split('","')]
                if len(parts) >= 2 and parts[1].isdigit():
                    pids.append(int(parts[1]))
            return pids
        out = subprocess.run(
            ["pgrep", "-f", "thunderbird"], capture_output=True, text=True, check=False
        ).stdout
        return [int(line) for line in out.split() if line.isdigit()]
    except (OSError, ValueError):
        return []


def running_command_lines() -> list[str]:
    """Command lines of running Thunderbird processes, best effort."""
    try:
        if sys.platform == "win32":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='thunderbird.exe'\" | ForEach-Object CommandLine",
            ]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            command = ["ps", "-A", "-o", "args="]
            flags = 0
        lines = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            creationflags=flags,
        ).stdout.splitlines()
        return [line for line in lines if "thunderbird" in line.lower()]
    except (OSError, subprocess.SubprocessError):
        return []


def saved_user_variable(name: str) -> str | None:
    """Read variables set after the calling program started from the Windows registry."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return value if isinstance(value, str) and value else None
    except OSError:
        return None


def is_running() -> bool:
    return bool(running_pids())


def open_windows() -> list[tuple[int, str]]:
    """Visible top-level windows owned by running Thunderbird processes."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    pids = set(running_pids())
    if not pids:
        return []
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    windows: list[tuple[int, str]] = []

    @callback_type
    def collect(handle, _data):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(handle):
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(handle) + 1)
            user32.GetWindowTextW(handle, title, len(title))
            windows.append((handle, title.value))
        return True

    # ponytail: visible windows only; tray-hidden Thunderbird needs a different close path.
    user32.EnumWindows(collect, 0)
    return windows


def _ask_to_close(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW(handle, 0x0010, 0, 0)


def _launch(exe: pathlib.Path, extra_args: list[str], profile: ThunderbirdProfile | None) -> None:
    argv = [str(exe), *extra_args]
    if profile is not None:
        argv += ["-profile", str(profile.path)]
    # All three streams, not just stdin. Thunderbird outlives us on purpose, so an
    # inherited stdout keeps the caller's pipe open long after `install-addon` has
    # finished — an agent or CI step capturing our output waits for the mail client
    # to be closed, and its own timeout cannot rescue it, because killing us leaves
    # the grandchild holding the pipe. `close_fds` does not cover the std handles;
    # they have to be redirected explicitly, as the daemon spawn in bridge.py does.
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def start_thunderbird() -> None:
    """Start the configured Thunderbird executable as a detached process."""
    executable = find_thunderbird()
    if executable is None:
        raise TransportError(
            "Thunderbird executable was not found. Configure TBMCP_THUNDERBIRD or install Thunderbird.",
            code="THUNDERBIRD_NOT_FOUND",
        )
    try:
        _launch(executable, [], None)
    except OSError as exc:
        raise TransportError(
            f"could not start Thunderbird: {exc}", code="THUNDERBIRD_START_FAILED"
        ) from exc


def _stop(timeout: float = 60.0) -> bool:
    """Ask Thunderbird to close, then wait. Never force-kills: that loses mail state."""
    if not is_running():
        return True
    if sys.platform == "win32":
        # taskkill without /F asks only one window per process; a mail opened in
        # its own window kept Thunderbird alive in command run #1083.
        for handle, _title in open_windows():
            _ask_to_close(handle)
    else:
        for pid in running_pids():
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running():
            return True
        time.sleep(1.0)
    return not is_running()


def _wont_close_message() -> str:
    if sys.platform == "win32":
        windows = open_windows()
        if windows:
            titles = ", ".join(f'"{title or "(untitled)"}"' for _handle, title in windows)
            return (
                f"Thunderbird would not close; still open: {titles}. Finish or close them and "
                "run this again, or use `tbmcp install-addon --manual`."
            )
        return (
            "Thunderbird would not close: no visible windows remain but the process is still "
            "running. Wait a moment and run this again, or use `tbmcp install-addon --manual`."
        )
    return (
        "Thunderbird would not close. Close it yourself and run this again, or use "
        "`tbmcp install-addon --manual`."
    )

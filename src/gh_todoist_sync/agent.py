"""The launchd agent that runs the sync on a timer."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "local.gh-todoist-sync"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / "Library/Logs/gh-todoist-sync.log"
DEFAULT_INTERVAL = 120

# launchd hands the job a minimal PATH, so `gh` and `td` -- which the token
# lookup shells out to -- have to be findable.
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _executable() -> str:
    found = shutil.which("gh-todoist-sync")
    return found or str(Path(sys.argv[0]).resolve())


def plist(interval: int = DEFAULT_INTERVAL) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [_executable(), "sync"],
        "EnvironmentVariables": {"PATH": PATH},
        "StartInterval": interval,
        # Without this the first run is one whole interval after login, which
        # wastes the moment the machine is most likely to be out of date.
        "RunAtLoad": True,
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        # Background keeps it off the foreground scheduler; the run is almost
        # entirely network wait, so it should never compete with real work.
        "ProcessType": "Background",
        "LowPriorityIO": True,
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def is_loaded() -> bool:
    return _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0


def install(interval: int = DEFAULT_INTERVAL) -> Path:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(plist(interval)))
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    result = _launchctl("bootstrap", _domain(), str(PLIST))
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return PLIST


def uninstall() -> None:
    if is_loaded():
        _launchctl("bootout", f"{_domain()}/{LABEL}")
    PLIST.unlink(missing_ok=True)


def describe() -> str:
    if not PLIST.exists():
        return "not installed"
    interval = plistlib.loads(PLIST.read_bytes()).get("StartInterval", "?")
    if not is_loaded():
        return f"installed but not loaded (every {interval}s)"
    printed = _launchctl("print", f"{_domain()}/{LABEL}").stdout
    exit_line = next(
        (line.strip() for line in printed.splitlines() if "last exit" in line.lower()),
        "last exit code = ?",
    )
    return f"running every {interval}s, {exit_line}"

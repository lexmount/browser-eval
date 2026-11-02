"""Own host capabilities that only macOS provides.

Features that need them call this module, so an unsupported host stops before any
measurement with an error that names the feature. `ps -axo` and `fcntl` are not
listed: they work on every POSIX host that this repository supports.
"""
import platform
import shutil
from pathlib import Path

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")


class MacOSRequiredError(ValueError):
    """A macOS-only feature was requested on a host that cannot provide it."""


def require_macos(feature: str, tools: tuple[str, ...] = ()) -> None:
    """Reject another operating system or a missing macOS command line tool."""
    system = platform.system()
    if system != "Darwin":
        raise MacOSRequiredError(f"{feature} requires macOS; this host runs {system or 'unknown'}")
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if missing:
        raise MacOSRequiredError(f"{feature} requires macOS tools that are missing: {', '.join(missing)}")


def sandbox_exec_available() -> bool:
    return platform.system() == "Darwin" and SANDBOX_EXEC.is_file()


def sandbox_exec_command(profile: str) -> list[str]:
    """Return the command prefix that applies a macOS sandbox profile."""
    require_macos("sandbox-exec network isolation")
    if not SANDBOX_EXEC.is_file():
        raise MacOSRequiredError(f"sandbox-exec network isolation requires {SANDBOX_EXEC}")
    return [str(SANDBOX_EXEC), "-p", profile]

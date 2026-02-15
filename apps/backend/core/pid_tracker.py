"""
PID file tracking for robust process detection.

Provides atomic PID file operations to prevent race conditions
and ensure reliable process tracking across parent/child transitions.

This module is used to track the current running process for a task,
allowing the frontend to detect if a process is running even when
the process map tracking is lost (e.g., during os.execv() transitions
on Windows).

Usage:
    from core.pid_tracker import write_pid, read_pid, clear_pid, is_pid_running

    # At start of task execution
    write_pid(spec_dir)

    # To check if tracked process is running
    pid = read_pid(spec_dir)
    if pid and is_pid_running(pid):
        print("Task is running")

    # At end of task execution (also registered as atexit handler)
    clear_pid(spec_dir)
"""

import atexit
import os
import tempfile
from pathlib import Path

PID_FILENAME = ".current_pid"
_cleanup_registered: set[Path] = set()


def write_pid(spec_dir: Path) -> None:
    """
    Write current process PID to spec directory atomically.

    Uses atomic write (write to temp, then rename) to prevent
    partial reads by frontend. Also registers atexit handler
    to clean up PID file on process exit.

    Args:
        spec_dir: The spec directory where PID file should be written
    """
    pid_file = spec_dir / PID_FILENAME
    pid_content = str(os.getpid())

    # Atomic write: write to temp file, then rename
    # This prevents race conditions where frontend reads partial content
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=spec_dir, prefix=".pid_tmp_")
        os.write(fd, pid_content.encode())
        os.close(fd)
        fd = None  # Mark as closed
        os.replace(tmp_path, pid_file)  # Atomic on POSIX and Windows
        tmp_path = None  # Mark as moved
    except Exception:
        # Clean up temp file on failure
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise

    # Register cleanup on process exit
    _register_cleanup(spec_dir)


def read_pid(spec_dir: Path) -> int | None:
    """
    Read PID from spec directory if exists and valid.

    Args:
        spec_dir: The spec directory to read PID from

    Returns:
        The PID as integer, or None if file doesn't exist or is invalid
    """
    pid_file = spec_dir / PID_FILENAME
    if not pid_file.exists():
        return None
    try:
        content = pid_file.read_text().strip()
        return int(content) if content else None
    except (ValueError, OSError):
        return None


def clear_pid(spec_dir: Path) -> None:
    """
    Remove PID file from spec directory.

    Safe to call multiple times - silently ignores if file doesn't exist.

    Args:
        spec_dir: The spec directory to clear PID from
    """
    pid_file = spec_dir / PID_FILENAME
    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass  # Best effort cleanup
    _cleanup_registered.discard(spec_dir)


def is_pid_running(pid: int) -> bool:
    """
    Check if a process with given PID is running.

    Uses os.kill(pid, 0) which doesn't actually kill the process,
    just checks if it exists.

    Args:
        pid: The process ID to check

    Returns:
        True if process is running, False otherwise
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
        return True
    except OSError:
        return False


def _register_cleanup(spec_dir: Path) -> None:
    """
    Register atexit handler to clean up PID file.

    Only registers once per spec_dir to avoid duplicate cleanups.

    Args:
        spec_dir: The spec directory to register cleanup for
    """
    if spec_dir not in _cleanup_registered:
        _cleanup_registered.add(spec_dir)
        # Use a closure to capture spec_dir value
        atexit.register(lambda sd=spec_dir: clear_pid(sd))

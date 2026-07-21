#!/usr/bin/env python3
"""Environment detection for Windows Agent Compatibility layer.

Outputs a JSON describing the current Windows shell environment, available
tools, encoding, and long-path support.
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Default cache TTL in seconds
DEFAULT_TTL = 300

# Cache file location
_CACHE_FILE = Path(tempfile.gettempdir()) / "windows-runtime-detect.json"


def _configure_utf8_stdout() -> None:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )


class EnvDetect:
    """Detect Windows shell environment."""

    SHELL_PRIORITY = ["pwsh", "powershell", "cmd", "bash"]

    def __init__(self) -> None:
        self._info: dict[str, Any] = {}

    def detect(self, force: bool = False, ttl: int = DEFAULT_TTL) -> dict[str, Any]:
        """Detect environment, using a file-based cache to avoid repeated subprocess calls.

        Args:
            force: Bypass cache and re-detect.
            ttl: Cache validity in seconds (default 300).
        """
        if not force:
            cached = self._read_cache(ttl)
            if cached is not None:
                self._info = cached
                return cached
        self._info = self._detect_full()
        self._write_cache(self._info)
        return self._info

    def _detect_full(self) -> dict[str, Any]:
        return {
            "os": self._detect_os(),
            "shell": self._detect_shell(),
            "encoding": self._detect_encoding(),
            "path_tools": self._detect_path_tools(),
            "long_path_support": self._detect_long_path_support(),
            "capabilities": self._detect_capabilities(),
        }

    def _read_cache(self, ttl: int) -> dict[str, Any] | None:
        """Read cached detection if it exists and is within TTL."""
        try:
            if not _CACHE_FILE.exists():
                return None
            raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            ts = raw.get("timestamp", 0)
            if time.time() - ts > ttl:
                return None
            return raw.get("data")
        except Exception:  # noqa: BLE001
            return None

    def _write_cache(self, data: dict[str, Any]) -> None:
        """Write detection results to cache file."""
        try:
            payload = {"timestamp": time.time(), "data": data}
            _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass  # Cache write failure is non-fatal

    def _detect_os(self) -> dict[str, str]:
        return {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        }

    def _detect_shell(self) -> dict[str, Any]:
        shells = {}
        for shell in self.SHELL_PRIORITY:
            exe = self._find_shell(shell)
            version = None
            if exe:
                version = self._get_shell_version(shell, exe)
            shells[shell] = {"available": exe is not None, "path": exe, "version": version}

        preferred = self._choose_preferred(shells)
        return {
            "available": shells,
            "preferred": preferred,
        }

    def _find_shell(self, shell: str) -> str | None:
        """Find a real shell executable, filtering out false positives like bash.CMD."""
        exe = shutil.which(shell) or shutil.which(shell + ".exe")
        if not exe:
            return None
        if shell == "bash":
            lower = exe.lower()
            if lower.endswith("bash.cmd") or lower.endswith("bash.bat"):
                return None
        return exe

    def _get_shell_version(self, shell: str, exe: str) -> str | None:
        if shell in ("pwsh", "powershell"):
            try:
                result = subprocess.run(
                    [exe, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="replace",
                )
                return result.stdout.strip() if result.returncode == 0 else None
            except Exception:  # noqa: BLE001
                return None
        if shell == "bash":
            try:
                result = subprocess.run(
                    [exe, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.stdout:
                    return result.stdout.splitlines()[0].strip()
            except Exception:  # noqa: BLE001
                return None
        return None

    def _choose_preferred(self, shells: dict[str, Any]) -> str | None:
        for shell in self.SHELL_PRIORITY:
            if shells[shell]["available"]:
                return shell
        return None

    def _detect_encoding(self) -> dict[str, Any]:
        encoding = {
            "python_default": sys.getdefaultencoding(),
            "stdout": sys.stdout.encoding if sys.stdout else None,
        }
        try:
            result = subprocess.run(
                ["cmd.exe", "/C", "chcp"],
                capture_output=True,
                timeout=5,
                encoding=None,
            )
            if result.returncode == 0:
                raw = self._decode(result.stdout)
                # Typical output: "Active code page: 936"
                for part in raw.replace("：", ":").split(":"):
                    stripped = part.strip()
                    if stripped.isdigit():
                        encoding["cmd_codepage"] = stripped
                        break
                else:
                    encoding["cmd_codepage"] = raw.strip()
        except Exception:  # noqa: BLE001
            pass
        return encoding

    def _decode(self, data: bytes) -> str:
        for enc in ("utf-8", "gbk", "cp1252"):
            try:
                return data.decode(enc, errors="replace")
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def _detect_path_tools(self) -> dict[str, str | None]:
        tools = ["python", "python3", "node", "npm", "git", "uv", "pip", "pip3", "pwsh", "bash"]
        result: dict[str, str | None] = {}
        for tool in tools:
            if tool == "bash":
                # Use _find_shell to filter out false positives like bash.CMD
                result[tool] = self._find_shell("bash")
            else:
                result[tool] = shutil.which(tool)
        return result

    def _detect_long_path_support(self) -> bool | None:
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\FileSystem",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
                return bool(value)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Capability detection (extended): tool versions + platform features
    # ------------------------------------------------------------------
    def _tool_version(self, tool: str, args: list[str], pattern: str = r"([\d][\d.]*)") -> Any:
        """Return the detected version string of a CLI tool, or None if absent.

        Returns ``True`` when the tool exists but its version cannot be parsed
        (so callers still know the capability is present).
        """
        exe = shutil.which(tool)
        if not exe:
            return None
        try:
            r = subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                timeout=3,
                encoding="utf-8",
                errors="replace",
            )
            out = (r.stdout or r.stderr).strip()
            m = re.search(pattern, out)
            if m:
                return m.group(1)
            return out.splitlines()[0] if out else True
        except Exception:  # noqa: BLE001
            return True

    def _detect_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "python": self._tool_version("python", ["--version"]) or self._tool_version("python3", ["--version"]),
            "node": self._tool_version("node", ["--version"]),
            "npm": self._tool_version("npm", ["--version"]),
            "git": self._tool_version("git", ["--version"]),
            "cargo": self._tool_version("cargo", ["--version"]),
            "go": self._tool_version("go", ["version"]),
            "java": self._tool_version("java", ["-version"]),
            "docker": self._tool_version("docker", ["--version"]),
        }
        caps["wsl"] = self._detect_wsl()
        caps["admin"] = self._detect_admin()
        caps["network"] = self._detect_network()
        return caps

    def _detect_wsl(self) -> bool:
        """True only when a WSL distro is actually installed (not just the stub)."""
        exe = shutil.which("wsl")
        if not exe:
            return False
        try:
            r = subprocess.run(
                [exe, "-l", "-q"],
                capture_output=True,
                text=True,
                timeout=3,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                return False
            return bool([l for l in r.stdout.splitlines() if l.strip()])
        except Exception:  # noqa: BLE001
            return False

    def _detect_admin(self) -> bool | None:
        """Best-effort elevation check (Windows only)."""
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return None

    def _detect_network(self) -> bool:
        """Best-effort outbound connectivity probe."""
        import socket
        try:
            socket.setdefaulttimeout(2)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect(("8.8.8.8", 53))
            return True
        except Exception:  # noqa: BLE001
            return False


def main() -> int:
    _configure_utf8_stdout()
    env = EnvDetect()
    info = env.detect()
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

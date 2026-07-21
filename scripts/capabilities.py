#!/usr/bin/env python3
"""Capabilities Manifest for the Windows Agent Runtime.

Produces a declarative manifest describing which operations the agent should
route through the Runtime's native wrappers (avoiding shell translation
entirely) versus which still require a shell. This lets an agent decide *how*
to act during planning, reducing model reasoning cost and failure rate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from env_detect import EnvDetect


def build_manifest(env: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the capabilities manifest.

    ``env`` may be a pre-computed EnvDetect().detect() result; if None it is
    computed fresh.
    """
    if env is None:
        env = EnvDetect().detect()

    caps = env.get("capabilities", {})
    preferred_shell = env.get("shell", {}).get("preferred")

    # Operations the Runtime can perform natively (no shell translation).
    # These are always available because tool_wrap uses the Python stdlib.
    native = {
        "supports_native_write": True,
        "supports_native_append": True,
        "supports_native_read": True,
        "supports_native_copy": True,
        "supports_native_move": True,
        "supports_native_mkdir": True,
        "supports_native_rm": True,
        "supports_native_grep": True,
        "supports_native_find": True,
        "supports_native_env": True,
        "supports_native_path": True,
    }

    # Toolchain capabilities derived from detection.
    # Preserve the detected version string when present; False when absent.
    toolchain = {
        "python": caps.get("python") or False,
        "node": caps.get("node") or False,
        "npm": caps.get("npm") or False,
        "git": caps.get("git") or False,
        "cargo": caps.get("cargo") or False,
        "go": caps.get("go") or False,
        "java": caps.get("java") or False,
        "docker": caps.get("docker") or False,
        "wsl_distro": bool(caps.get("wsl")),
    }

    platform_feats = {
        "preferred_shell": preferred_shell,
        "admin": caps.get("admin"),
        "network": caps.get("network"),
        "long_path_support": env.get("long_path_support"),
    }

    return {
        "version": 1,
        "runtime": "windows-agent-compat",
        "native": native,
        "toolchain": toolchain,
        "platform": platform_feats,
        "guidance": {
            "use_wrap_for": [k.replace("supports_native_", "") for k, v in native.items() if v],
            "use_shell_for": ["grep_pipe_chains", "compound_logic", "process_management"],
            "note": "Prefer `wrap <op>` for file ops; use `exec` only when a shell is genuinely needed.",
        },
    }


def main() -> int:
    import io

    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

    env = EnvDetect().detect()
    print(json.dumps(build_manifest(env), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

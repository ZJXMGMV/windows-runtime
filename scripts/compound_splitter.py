#!/usr/bin/env python3
"""Compound command splitter for the translation engine.

Splits compound Bash commands (using &&, ||, ;) into individual segments,
translates each segment independently, then reassembles with the correct
operator for the target shell.

Pipes (|) are NOT split here — they are handled by dedicated rules in
adapters.json (e.g. "cat | grep") or passed through to the shell which
natively supports piping (pwsh, cmd, bash all support |).
"""
from __future__ import annotations

import re
from typing import Any


# Operators we split on, in priority order.
# We do NOT split on single | (pipe) — that's shell-native.
_OPERATORS = ["&&", "||", ";"]

# Regex to detect if a command contains any compound operator outside quotes.
_COMPOUND_RE = re.compile(
    r"""
    (?:^|[^&|])   # not preceded by & or | (avoid matching inside &&/||)
    (&&|\|\||;)   # the operator
    """,
    re.VERBOSE,
)


def _is_quoted_or_escaped(cmd: str, pos: int) -> bool:
    """Check if position `pos` is inside single/double quotes."""
    in_single = False
    in_double = False
    i = 0
    while i < pos:
        ch = cmd[i]
        if ch == "\\" and i + 1 < len(cmd) and not in_single:
            i += 2  # skip escaped char
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        i += 1
    return in_single or in_double


def split_compound(cmd: str) -> list[tuple[str, str]]:
    """Split a compound command into segments.

    Returns a list of (segment, operator) tuples where operator is the
    operator that FOLLOWS the segment (empty string for the last segment).

    Examples:
        "mkdir -p ./a && cd ./a" -> [("mkdir -p ./a", "&&"), ("cd ./a", "")]
        "export A=1; echo $A"    -> [("export A=1", ";"), ("echo $A", "")]
        "cat f.txt"              -> [("cat f.txt", "")]  (no split)
    """
    cmd = cmd.strip()
    if not cmd:
        return []

    segments: list[tuple[str, str]] = []
    current_start = 0
    i = 0

    while i < len(cmd):
        # Check for operators at position i
        matched_op = None
        op_len = 0

        if cmd[i:i+2] in ("&&", "||") and not _is_quoted_or_escaped(cmd, i):
            matched_op = cmd[i:i+2]
            op_len = 2
        elif cmd[i] == ";" and not _is_quoted_or_escaped(cmd, i):
            matched_op = ";"
            op_len = 1

        if matched_op:
            segment = cmd[current_start:i].strip()
            if segment:
                segments.append((segment, matched_op))
            current_start = i + op_len
            i = current_start
        else:
            i += 1

    # Last segment (no trailing operator)
    last = cmd[current_start:].strip()
    if last:
        segments.append((last, ""))

    return segments


def is_compound(cmd: str) -> bool:
    """Quick check: does this command contain compound operators outside quotes?"""
    for i in range(len(cmd)):
        if cmd[i:i+2] in ("&&", "||") and not _is_quoted_or_escaped(cmd, i):
            return True
        if cmd[i] == ";" and not _is_quoted_or_escaped(cmd, i):
            return True
    return False


def reassemble(
    translated_segments: list[dict[str, Any]],
    shell: str,
) -> str:
    """Reassemble translated segments with the correct operators for the target shell.

    Each element in translated_segments is a dict with at least:
        - "translated": the translated command string
        - "operator": the operator following this segment ("&&", "||", ";", or "")

    Shell-specific operator mapping:
        - pwsh (7+): &&, ||, ; all native
        - powershell (5): && -> ; if ($?) { next }; || -> ; if (-not $?) { next }
          (but this is complex — for PS5 we fall back to ; with a warning comment)
        - cmd: && and || native; ; -> & (cmd has no ;)
        - bash: all native (passthrough)
    """
    if not translated_segments:
        return ""

    parts: list[str] = []
    shell_key = "pwsh" if shell in ("pwsh", "powershell") else shell

    for seg in translated_segments:
        translated = seg["translated"]
        op = seg.get("operator", "")

        parts.append(translated)

        if not op:
            continue

        if shell_key == "cmd":
            # cmd.exe: && and || are native; ; doesn't exist, use &
            if op == ";":
                parts.append("&")
            else:
                parts.append(op)
        elif shell_key in ("pwsh", "powershell"):
            # pwsh 7+: all operators native
            # powershell 5: && and || not supported, degrade to ;
            if shell == "powershell" and op in ("&&", "||"):
                # PowerShell 5 doesn't support &&/||; use ; as best-effort
                parts.append(";")
            else:
                parts.append(op)
        else:
            # bash or unknown: passthrough
            parts.append(op)

    return " ".join(parts)

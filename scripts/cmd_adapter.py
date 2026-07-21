#!/usr/bin/env python3
"""Command translator for Windows Agent Compatibility layer.

Translates Bash-style commands into the target Windows shell syntax.
PowerShell 7 is the preferred target; cmd and Git Bash are supported as
fallbacks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from compound_splitter import is_compound, split_compound, reassemble


class CommandTranslator:
    """Translate Bash-style commands to Windows shell commands."""

    # Alias: "powershell" uses the same templates as "pwsh"
    SHELL_ALIASES = {"powershell": "pwsh"}

    def _resolve_shell_key(self, shell: str) -> str:
        """Resolve a shell name to its template key in the config."""
        return self.SHELL_ALIASES.get(shell, shell)

    def __init__(self, config_path: Path | None = None) -> None:
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "adapters.json"
        self.config = self._load_config(config_path)
        self.rules = self.config.get("commands", {})

    def _load_config(self, path: Path) -> dict[str, Any]:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def translate(self, cmd: str, shell: str = "pwsh") -> dict[str, Any]:
        """Translate a Bash-style command to the target Windows shell syntax.

        Handles compound commands (&&, ||, ;) by splitting, translating each
        segment independently, and reassembling with shell-appropriate operators.

        Also handles simple redirections (>, >>, 2>&1) by stripping them,
        translating the core command, then re-appending the redirect.

        Returns a dict with keys: original, translated, shell, matched_rule, fallback.
        For compound commands, matched_rule is "compound" and fallback is False.
        """
        cmd = cmd.strip()
        if not cmd:
            return {
                "original": cmd,
                "translated": cmd,
                "shell": shell,
                "matched_rule": None,
                "fallback": False,
            }

        # Extract trailing redirections (>, >>, 2>&1, &>)
        redirect_suffix = ""
        redir_match = re.search(r'\s+(>>?\s*\S+|2>&1|&>\s*\S+)(\s+2>&1)?$', cmd)
        if redir_match:
            redirect_suffix = redir_match.group(0).strip()

        if redirect_suffix:
            core_cmd = cmd[:cmd.rfind(redirect_suffix)].strip()
            # Translate core command only
            result = self.translate(core_cmd, shell)
            if result["translated"] != core_cmd:  # A rule matched
                translated_redir = self._translate_redirect(redirect_suffix, shell)
                result["translated"] += f" {translated_redir}"
                return result
            # Fallback: keep original
            return {"original": cmd, "translated": cmd, "shell": shell, "matched_rule": None, "fallback": True}

        # Compound command: split, translate each segment, reassemble
        if is_compound(cmd):
            segments = split_compound(cmd)
            if len(segments) > 1:
                translated_segments = []
                any_matched = False
                for segment_cmd, operator in segments:
                    seg_result = self._translate_single(segment_cmd, shell)
                    any_matched = any_matched or (seg_result["matched_rule"] is not None)
                    translated_segments.append({
                        "translated": seg_result["translated"],
                        "operator": operator,
                    })
                reassembled = reassemble(translated_segments, shell)
                return {
                    "original": cmd,
                    "translated": reassembled,
                    "shell": shell,
                    "matched_rule": "compound" if any_matched else None,
                    "fallback": not any_matched,
                }

        return self._translate_single(cmd, shell)

    def _translate_single(self, cmd: str, shell: str) -> dict[str, Any]:
        """Translate a single (non-compound) command."""
        shell_key = self._resolve_shell_key(shell)

        # Try exact adapters first
        for rule_name, rule in self.rules.items():
            pattern = rule.get("pattern")
            if not pattern:
                continue
            match = re.match(pattern, cmd, re.IGNORECASE)
            if match:
                template = rule.get(shell_key, rule.get("bash", cmd))
                translated = self._render(template, match.groupdict(), rule_name=rule_name)
                
                return {
                    "original": cmd,
                    "translated": translated,
                    "shell": shell,
                    "matched_rule": rule_name,
                    "fallback": False,
                }

        # Fallback: path/quote normalization for the target shell
        fallback_cmd = self._normalize(cmd, shell)
        return {
            "original": cmd,
            "translated": fallback_cmd,
            "shell": shell,
            "matched_rule": None,
            "fallback": True,
        }

    def _render(self, template: str, values: dict[str, str], rule_name: str = None) -> str:
        # Escape $ and ` inside values for PowerShell double-quoted strings
        if rule_name == "export":
            values = {k: v.replace("$", "$$").replace("`", "``") for k, v in values.items()}
        
        # For echo literal, strip bash-style outer quotes and handle content properly
        if rule_name == "echo literal":
            text = values.get("text", "")
            # Strip outer double quotes (bash "..." -> just the content)
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            # Strip outer single quotes (bash '...' -> just the content)
            elif text.startswith("'") and text.endswith("'"):
                text = text[1:-1]
            values["text"] = text
        
        result = template
        for key, value in values.items():
            result = result.replace("{{" + key + "}}", value)
        # Clean up empty -Path '' and -Path "" parameters that result from empty captures
        import re as _re
        result = _re.sub(r"\s+-Path\s+''(\s|$)", r"\1", result)
        result = _re.sub(r"\s+-Path\s+\"\"(\s|$)", r"\1", result)
        # Clean up a trailing stray empty single-quote pair left by an empty
        # capture (e.g. bash `ls -la ''`). Anchored to end-of-string so it never
        # touches an intentional empty arg mid-template (e.g. cmd `find /C /V ""`).
        result = _re.sub(r"\s+''\s*$", "", result)
        # Clean up double spaces left by empty substitutions
        result = _re.sub(r"  +", " ", result)
        result = result.strip()
        
        return result

    def _normalize(self, cmd: str, shell: str) -> str:
        """Minimal normalization for unmatched commands."""
        if shell == "cmd":
            return cmd.replace("/", "\\")
        if shell in ("pwsh", "powershell"):
            # Preserve forward slashes but normalize double backslashes
            return cmd.replace("\\\\", "\\")
        return cmd

    def _translate_redirect(self, suffix: str, shell: str) -> str:
        """Translate a bash-style redirect suffix into shell-native syntax.

        For cmd: keep as-is (cmd natively supports >, >>, 2>&1).
        For pwsh/powershell: convert to pipeline Out-File or native PS operators.
        """
        if shell not in ("pwsh", "powershell"):
            return suffix

        suffix = suffix.strip()

        # &> file  →  *> file  (PS 7 all-stream redirect)
        m = re.match(r'^&>\s*(\S+)$', suffix)
        if m:
            return f"*> {m.group(1)}"

        # 2>&1 alone (no file target) — valid in PS as-is
        if suffix == "2>&1":
            return "2>&1"

        # > file 2>&1  or  >> file 2>&1
        m = re.match(r'^(>>?)\s*(\S+)\s+2>&1$', suffix)
        if m:
            op, target = m.group(1), m.group(2)
            append = " -Append" if op == ">>" else ""
            return f"2>&1 | Out-File -FilePath '{target}'{append} -Encoding utf8"

        # > file  or  >> file
        m = re.match(r'^(>>?)\s*(\S+)$', suffix)
        if m:
            op, target = m.group(1), m.group(2)
            append = " -Append" if op == ">>" else ""
            return f"| Out-File -FilePath '{target}'{append} -Encoding utf8"

        # Unrecognized pattern — return as-is
        return suffix

    def list_rules(self) -> list[str]:
        return list(self.rules.keys())


def main() -> int:
    import sys

    if len(sys.argv) < 2:
        print("Usage: cmd_adapter.py <command> [shell=pwsh]")
        return 1

    cmd = sys.argv[1]
    shell = sys.argv[2] if len(sys.argv) > 2 else "pwsh"

    translator = CommandTranslator()
    result = translator.translate(cmd, shell=shell)
    print(result["translated"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

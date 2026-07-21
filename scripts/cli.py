#!/usr/bin/env python3
"""Main CLI for Windows Agent Compatibility skill."""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from cmd_adapter import CommandTranslator
from env_detect import EnvDetect
from exec_runner import ExecRunner
from output_parser import OutputParser
from prompt_gen import generate_prompt
from capabilities import build_manifest
from tool_wrap import OPERATIONS


def _configure_utf8_stdout() -> None:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )


def cmd_detect(args: argparse.Namespace) -> int:
    env = EnvDetect().detect()
    if args.json:
        print(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        shell = env.get("shell", {})
        print(f"OS: {env['os']['system']} {env['os']['release']}")
        print(f"Preferred shell: {shell.get('preferred', 'unknown')}")
        print("Available shells:")
        for name, info in shell.get("available", {}).items():
            print(f"  {name}: {info['available']} ({info['path'] or 'not found'})")
        print(f"Long paths: {env.get('long_path_support', 'unknown')}")
    return 0


def _strip_trailing_flags(raw: str) -> str:
    """Strip trailing --shell/--cwd/--retries/--json flags that argparse may bundle."""
    import re as _re
    return _re.sub(r"\s+--(shell|cwd|retries|json)(?:\s+\S+)?\s*$", "", raw).strip()


def _default_shell() -> str:
    """Pick a shell that actually exists on this system."""
    try:
        from env_detect import EnvDetect
        preferred = EnvDetect().detect().get("shell", {}).get("preferred")
        if preferred:
            return preferred
    except Exception:  # noqa: BLE001
        pass
    return "pwsh"


def cmd_translate(args: argparse.Namespace) -> int:
    translator = CommandTranslator()
    raw = _strip_trailing_flags(args.command)
    shell = args.shell if args.shell else _default_shell()
    result = translator.translate(raw, shell=shell)
    print(result["translated"])
    return 0


def cmd_exec(args: argparse.Namespace) -> int:
    shell = args.shell if args.shell else _default_shell()
    runner = ExecRunner(preferred_shell=shell)
    raw = _strip_trailing_flags(args.command)
    result = runner.run(raw, shell=shell, cwd=args.cwd, retries=args.retries)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        if result["stdout"]:
            print(result["stdout"])
        if result["stderr"]:
            print(result["stderr"], file=sys.stderr)
    return 0 if result["ok"] else result["exit_code"]


def cmd_prompt(args: argparse.Namespace) -> int:
    env = EnvDetect().detect() if not args.json else None
    if args.json:
        print(json.dumps({"prompt": generate_prompt(env)}, indent=2, ensure_ascii=False))
    else:
        print(generate_prompt(env))
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    env = EnvDetect().detect()
    manifest = build_manifest(env)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def cmd_wrap(args: argparse.Namespace) -> int:
    operation = args.operation
    if operation not in OPERATIONS:
        print(f"Unknown operation: {operation}", file=sys.stderr)
        print(f"Available: {', '.join(OPERATIONS.keys())}", file=sys.stderr)
        return 1
    # `write` cannot take multi-line content via CLI args (shell quoting mangles
    # newlines). Support `--from-file <path>` to read content from a file.
    if operation == "write":
        pos = [a for a in args.args if a != "--from-file"]
        if "--from-file" in args.args:
            idx = list(args.args).index("--from-file")
            src = args.args[idx + 1]
            content = Path(src).read_text(encoding="utf-8", errors="replace")
            func = OPERATIONS["write"]
            try:
                func(pos[0], content)
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        # no --from-file: single positional content still supported for simple text
        func = OPERATIONS["write"]
        try:
            func(*pos)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    func = OPERATIONS[operation]
    try:
        result = func(*args.args)
        if result is not None:
            print(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wincompat", description="Windows Agent Compatibility layer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="Detect current Windows environment")
    p_detect.add_argument("--json", action="store_true", help="Output JSON")
    p_detect.set_defaults(func=cmd_detect)

    p_translate = sub.add_parser("translate", help="Translate a Bash-style command to target shell")
    p_translate.add_argument("command", help="Command to translate")
    p_translate.add_argument("--shell", default=None, help="Target shell (pwsh/powershell/cmd/bash). Default: auto-detect")
    p_translate.set_defaults(func=cmd_translate)

    p_exec = sub.add_parser("exec", help="Translate and execute a command")
    p_exec.add_argument("command", help="Command to execute")
    p_exec.add_argument("--shell", default=None, help="Shell to use. Default: auto-detect")
    p_exec.add_argument("--cwd", default=None, help="Working directory")
    p_exec.add_argument("--retries", type=int, default=1, help="Retry count on failure")
    p_exec.add_argument("--json", action="store_true", help="Output JSON")
    p_exec.set_defaults(func=cmd_exec)

    p_prompt = sub.add_parser("prompt", help="Generate environment prompt fragment")
    p_prompt.add_argument("--json", action="store_true", help="Output JSON")
    p_prompt.set_defaults(func=cmd_prompt)

    p_wrap = sub.add_parser("wrap", help="Run a safe tool wrapper")
    p_wrap.add_argument("operation", help=f"One of: {', '.join(OPERATIONS.keys())}")
    p_wrap.add_argument("args", nargs=argparse.REMAINDER, help="Arguments for the operation")
    p_wrap.set_defaults(func=cmd_wrap)

    p_cap = sub.add_parser("capabilities", help="Emit capabilities manifest for agent planning")
    p_cap.set_defaults(func=cmd_capabilities)

    return parser


def main() -> int:
    _configure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

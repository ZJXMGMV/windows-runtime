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
from tool_wrap import OPERATIONS, grep_from_args
from path_resolver import PathResolver
from tool_discovery import ToolDiscovery
from recovery import RecoveryEngine


def _configure_utf8_stdout() -> None:
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )


def cmd_detect(args: argparse.Namespace) -> int:
    env = EnvDetect().detect(force=args.force)
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
    result = runner.run(raw, shell=shell, cwd=args.cwd, retries=args.retries, recover=not args.no_recover)
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


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        print(PathResolver().resolve(args.path))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    td = ToolDiscovery()
    if args.tool:
        print(json.dumps(td.resolve(args.tool), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(td.discover_all(), indent=2, ensure_ascii=False))
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    try:
        result = json.loads(args.result_json)
    except json.JSONDecodeError:
        print("Error: result_json must be valid JSON", file=sys.stderr)
        return 1
    print(json.dumps(RecoveryEngine().analyze(result), indent=2, ensure_ascii=False))
    return 0


def cmd_wrap(args: argparse.Namespace) -> int:
    operation = args.operation
    if operation not in OPERATIONS:
        print(f"Unknown operation: {operation}", file=sys.stderr)
        print(f"Available: {', '.join(OPERATIONS.keys())}", file=sys.stderr)
        return 1
    # `write` and `append` cannot take multi-line content via CLI args (shell quoting
    # mangles newlines). Support `--from-file <path>` to read content from a file.
    if operation in ("write", "append"):
        pos = [a for a in args.args if a != "--from-file"]
        if "--from-file" in args.args:
            idx = list(args.args).index("--from-file")
            src = args.args[idx + 1]
            content = Path(src).read_text(encoding="utf-8", errors="replace")
            func = OPERATIONS[operation]
            try:
                func(pos[0], content)
                return 0
            except Exception as exc:  # noqa: BLE001
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        # no --from-file: single positional content still supported for simple text
        func = OPERATIONS[operation]
        try:
            func(*pos)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    if operation == "grep":
        # Route through grep_from_args so the enhanced flags
        # (--context/--exclude-dir/--include/--case-sensitive) reach safe_grep.
        try:
            matches = grep_from_args(list(args.args))
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        for line in matches:
            print(line)
        return 0
    func = OPERATIONS[operation]
    try:
        result = func(*args.args)
        if result is not None:
            print(result)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Daemon mode: dispatch + serve
# ---------------------------------------------------------------------------

def dispatch(action: str, params: dict) -> dict:
    """Route an action to the appropriate handler and return a result dict.

    Used by both the `serve` daemon and potentially by programmatic callers.
    Returns {"ok": True, ...} on success or {"ok": False, "error": "..."} on failure.
    """
    try:
        if action == "detect":
            force = params.get("force", False)
            return {"ok": True, "env": EnvDetect().detect(force=force)}

        elif action == "translate":
            cmd = params.get("command", "")
            shell = params.get("shell") or _default_shell()
            translator = CommandTranslator()
            result = translator.translate(cmd, shell=shell)
            return {"ok": True, **result}

        elif action == "exec":
            cmd = params.get("command", "")
            shell = params.get("shell") or _default_shell()
            cwd = params.get("cwd")
            retries = params.get("retries", 1)
            recover = params.get("recover", True)
            runner = ExecRunner(preferred_shell=shell)
            result = runner.run(cmd, shell=shell, cwd=cwd, retries=retries, recover=recover)
            return {"ok": result["ok"], **result}

        elif action == "prompt":
            env = EnvDetect().detect()
            return {"ok": True, "prompt": generate_prompt(env)}

        elif action == "capabilities":
            env = EnvDetect().detect()
            return {"ok": True, "manifest": build_manifest(env)}

        elif action == "resolve":
            path = params.get("path", "")
            resolved = PathResolver().resolve(path)
            return {"ok": True, "resolved": resolved}

        elif action == "discover":
            td = ToolDiscovery()
            tool = params.get("tool")
            if tool:
                return {"ok": True, "tool": td.resolve(tool)}
            return {"ok": True, "tools": td.discover_all()}

        elif action == "recover":
            exec_result = params.get("result")
            if not isinstance(exec_result, dict):
                return {"ok": False, "error": "params.result must be a dict (ExecRunner result)"}
            analysis = RecoveryEngine().analyze(exec_result)
            return {"ok": True, **analysis}

        elif action == "wrap":
            operation = params.get("operation", "")
            op_args = params.get("args", [])
            if operation not in OPERATIONS:
                return {"ok": False, "error": f"Unknown operation: {operation}. Available: {', '.join(OPERATIONS.keys())}"}
            if operation == "grep":
                # Route through grep_from_args so the enhanced flags
                # (--context/--exclude-dir/--include/--case-sensitive) reach safe_grep.
                return {"ok": True, "matches": grep_from_args(list(op_args))}
            func = OPERATIONS[operation]
            result = func(*op_args)
            return {"ok": True, "result": result}

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def cmd_serve(args: argparse.Namespace) -> int:
    """Run as a JSON-line daemon on stdin/stdout.

    Protocol: one JSON object per line in, one JSON object per line out.
    Request:  {"action": "translate", "params": {"command": "ls -la", "shell": "pwsh"}}
    Response: {"ok": true, "translated": "Get-ChildItem ...", ...}
    Send {"action": "quit"} to shut down gracefully.
    """
    # Emit a ready banner so the caller knows the process is alive
    print(json.dumps({"ok": True, "ready": True, "actions": [
        "detect", "translate", "exec", "prompt", "capabilities",
        "resolve", "discover", "recover", "wrap", "quit",
    ]}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": f"Invalid JSON: {exc}"}), flush=True)
            continue

        action = request.get("action", "")
        if action == "quit":
            print(json.dumps({"ok": True, "bye": True}), flush=True)
            break

        params = request.get("params", {})
        response = dispatch(action, params)
        print(json.dumps(response, ensure_ascii=False), flush=True)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wincompat", description="Windows Agent Compatibility layer")
    sub = parser.add_subparsers(dest="command", required=True)

    p_detect = sub.add_parser("detect", help="Detect current Windows environment")
    p_detect.add_argument("--json", action="store_true", help="Output JSON")
    p_detect.add_argument("--force", action="store_true", help="Bypass cache and re-detect")
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
    p_exec.add_argument("--no-recover", action="store_true", help="Disable auto error-recovery")
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

    p_resolve = sub.add_parser("resolve", help="Normalize a path to canonical Windows form")
    p_resolve.add_argument("path", help="Path to resolve (supports ~, .., //UNC, /mnt/c, long paths)")
    p_resolve.set_defaults(func=cmd_resolve)

    p_discover = sub.add_parser("discover", help="Discover best executable for a tool")
    p_discover.add_argument("tool", nargs="?", default=None, help="Tool name (git/python/node/...). Omit for all")
    p_discover.set_defaults(func=cmd_discover)

    p_recover = sub.add_parser("recover", help="Analyze an exec result JSON and suggest recovery")
    p_recover.add_argument("result_json", help="ExecRunner result as JSON string")
    p_recover.set_defaults(func=cmd_recover)

    p_serve = sub.add_parser("serve", help="Run as a JSON-line daemon (stdin/stdout protocol)")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main() -> int:
    _configure_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

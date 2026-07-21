# Agent Workflow Examples

This file shows how an agent should use the `windows-runtime` skill on Windows. It is reference material — read it when you need concrete command patterns, not on every invocation.

## Scenario 1: Session start — environment awareness

Before running commands, detect the environment to decide shell strategy:

```powershell
python scripts/cli.py detect --json
```

Sample output (truncated):
```json
{
  "os": {"system": "Windows", "release": "10", "machine": "AMD64"},
  "shell": {
    "preferred": "powershell",
    "available": {"pwsh": {"available": false}, "powershell": {"available": true}, "cmd": {"available": true}, "bash": {"available": false}}
  },
  "encoding": {"cmd_codepage": "936"},
  "path_tools": {"python": "C:\\...\\python.exe", "git": "C:\\...\\git.exe"}
}
```

**Decision:** preferred shell is `powershell` (falls back to PS5 since no pwsh); codepage 936 means UTF-8 is forced at execution time.

**Caching:** `detect` probes many tool versions and is comparatively slow, so the
result is cached for 300s (TTL) in the system temp dir
(`%TEMP%\windows-runtime-detect.json`). Repeat calls are near-instant. Pass
`--force` to bypass the cache and re-probe (e.g. right after installing a new
toolchain):

```powershell
python scripts/cli.py detect --json --force
```

## Scenario 2: Before generating commands — translation check

Verify Bash-style commands translate correctly before executing:

```powershell
python scripts/cli.py translate "rm -rf ./build"
# → Remove-Item -Path './build' -Recurse -Force -ErrorAction SilentlyContinue | Out-Null

python scripts/cli.py translate "mkdir -p ./build/output"
# → if (-not (Test-Path './build/output')) { New-Item -ItemType Directory -Path './build/output' -Force | Out-Null }

python scripts/cli.py translate "cat app.log | grep ERROR"
# → Select-String -Path 'app.log' -Pattern 'ERROR' -CaseSensitive:$false
```

If you omit `--shell`, the detected preferred shell is used automatically.

## Scenario 3: Direct execution — translate + run

To inspect an env var or list a directory, use `exec` (auto-translate + UTF-8 execute + standard JSON):

```powershell
python scripts/cli.py exec "echo $PATH" --json
```

```json
{
  "ok": true,
  "stdout": "C:\\Python311\\Scripts;...",
  "stderr": "",
  "exit_code": 0,
  "shell_used": "powershell",
  "translated_cmd": "Write-Output $env:PATH",
  "matched_rule": "echo env",
  "fallback": false
}
```

Chinese paths render correctly (UTF-8 decode):
```powershell
python scripts/cli.py exec "ls ./项目文档" --json
```

## Scenario 4: Fragile operations — use wrap to bypass the shell

For delete / create / read / write / search, prefer `wrap` (Python-native via `pathlib`/`shutil`) over shell commands:

```powershell
python scripts/cli.py wrap rm ./old_cache
python scripts/cli.py wrap mkdir ./dist/assets
python scripts/cli.py wrap write ./config/settings.json "{\"key\": \"value\"}"
python scripts/cli.py wrap grep "TODO" "src/*.py"
python scripts/cli.py wrap find ./src "*.md"
```

## Scenario 5: Inject environment context into a downstream agent

When dispatching a sub-task to another agent (or building a system prompt for Codex CLI / OpenCode / Hermes), generate an environment fragment:

```powershell
python scripts/cli.py prompt
```

Output can be pasted directly into the sub-agent's system prompt (see assets/prompt-template.txt for the template).

## Scenario 6: Many commands in a row — daemon mode (serve)

Each `cli.py` invocation pays Python interpreter startup cost. When running many
commands back-to-back, start one long-lived process and talk to it over
stdin/stdout, one JSON object per line in each direction:

```powershell
python scripts/cli.py serve
```

On start it prints a ready banner so the caller knows the process is alive and
which actions are available:

```json
{"ok": true, "ready": true, "actions": ["detect", "translate", "exec", "wrap", "prompt", "capabilities", "resolve", "discover", "recover", "quit"]}
```

Send a request as `{"action": ..., "params": {...}}`; read back one response
line. The daemon routes every action through the same dispatcher the CLI uses, so
results are identical to the one-shot subcommands:

```json
{"action": "translate", "params": {"command": "ls -la"}}
{"ok": true, "translated": "Get-ChildItem -Force", "matched_rule": "ls", "fallback": false, "shell": "powershell"}

{"action": "resolve", "params": {"path": "/mnt/c/Users/me/foo"}}
{"ok": true, "resolved": "C:\\Users\\me\\foo"}
```

Errors come back on the same channel as `{"ok": false, "error": "..."}` (the
process stays alive — a bad request does not kill it):

```json
{"action": "wrap", "params": {"operation": "bogus", "args": []}}
{"ok": false, "error": "Unknown operation: bogus. Available: rm, mkdir, copy, move, read, write, append, grep, find, env, path"}
```

Shut down gracefully with `quit`:

```json
{"action": "quit"}
{"ok": true, "bye": true}
```

## Decision tree (agent built-in logic)

```
Receive a shell command request
  │
  ├─ Is it a fragile op (delete / mkdir / read / write / grep / find)?
  │     └─ Yes → use wrap subcommand (rm / mkdir / write / read / grep / find)
  │
  └─ No → needs execution?
        ├─ Yes → exec subcommand (auto-translate + UTF-8 + JSON)
        └─ No → only need syntax? → translate subcommand
```

Run `detect` + `prompt` once at session start; afterwards follow the tree with `wrap` / `exec` / `translate`.

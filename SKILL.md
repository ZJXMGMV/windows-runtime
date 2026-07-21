---
name: windows-agent-compat
description: Windows command-line compatibility layer for AI agents. Use when an agent needs to execute shell commands on Windows outside WSL, especially when the agent generates Linux/Bash-style commands (rm, cat, grep, export, chmod, etc.) that fail on Windows. Provides command translation, unified execution, safe file wrappers, and environment detection. Works with OpenClaw, Codex CLI, OpenCode, and Hermes via the Agent Skills standard.
---

# Windows Agent Compatibility

## Overview

AI agents are trained mostly on Linux/Bash examples and frequently fail on Windows because of shell differences, path formats, quoting, encoding, and permissions. This skill provides a compatibility layer that translates Bash-style commands into Windows-compatible commands, with PowerShell 7 as the preferred shell and fallback to Windows PowerShell 5 / cmd.

## When to use this skill

Use this skill when:
- An agent needs to run shell commands on Windows outside WSL.
- The agent generated a command like `rm -rf`, `cat ... | grep`, `export`, `chmod`, `find`, `head`, `tail`.
- A command failed due to shell syntax, path separators, quoting, encoding, or missing tools.
- You want to provide a system prompt fragment describing the current Windows environment.

## Quick start

1. Detect the environment:
   ```powershell
   python scripts/cli.py detect --json
   ```
2. Emit the capabilities manifest (for agent planning — which ops to route through `wrap`):
   ```powershell
   python scripts/cli.py capabilities
   ```
3. Translate a command to PowerShell syntax:
   ```powershell
   python scripts/cli.py translate "rm -rf ./temp"
   ```
4. Execute a translated command:
   ```powershell
   python scripts/cli.py exec "cat a.txt | grep hello"
   ```
5. Generate a system prompt fragment:
   ```powershell
   python scripts/cli.py prompt
   ```
6. Run a safe tool wrapper:
   ```powershell
   python scripts/cli.py wrap mkdir ./build
   ```
7. Resolve any path notation to a canonical Windows path:
   ```powershell
   python scripts/cli.py resolve "/mnt/c/Users/me/foo"
   ```
8. Discover the best executable for a tool:
   ```powershell
   python scripts/cli.py discover git
   ```

## Shell priority

| Priority | Shell | When used |
|----------|-------|------------|
| 1 | PowerShell 7 (`pwsh`) | Preferred if available |
| 2 | Windows PowerShell 5 | Fallback if `pwsh` is missing |
| 3 | `cmd.exe` | Fallback if no PowerShell |
| 4 | Git Bash (`bash`) | Lowest priority, for Linux toolchains only |

Note: `powershell` is aliased to `pwsh` internally. Both use the same templates.

## Command coverage (P0)

- File/directory: `rm -rf`, `rm -f`, `mkdir -p`, `cp -r`, `mv`, `touch`
- Listing: `ls`, `pwd`, `which`
- Find/filter: `cat`, `cat | grep`, `grep`, `find`, `head`, `tail`, `wc -l`
- Environment: `export`, `echo $PATH`, `unset`, `echo <text>`
- Path/quote handling: automatic separator and quoting fixes
- Toolchain: `python`, `node`, `npm`, `git`, `uv` detection
- Special: `chmod +x` (no-op on Windows)

For the full command registry, see [references/command-registry.md](references/command-registry.md).

## Tool wrappers (P1)

For operations that are fragile or error-prone across shells, use Python wrappers instead of generating shell commands:

- `safe_rm(path)` — remove files/directories
- `safe_mkdir(path)` — create directories recursively
- `safe_copy(src, dst)` / `safe_move(src, dst)`
- `safe_read(path)` / `safe_write(path, content)`
- `safe_grep(pattern, files)` / `safe_find(root, pattern)`
- `safe_env(name, value=None)` / `safe_path(path)`

See [references/tool-wrappers.md](references/tool-wrappers.md) for details.

## Capabilities manifest (P2)

`capabilities` emits a declarative manifest the agent should read during *planning* so it knows which ops to route through `wrap` (native, no shell) vs `exec` (needs a shell). The manifest exposes:

- `native` — which file ops are natively supported (always true; `tool_wrap` uses stdlib)
- `toolchain` — detected versions of python/node/npm/git/cargo/go/java/docker, plus `wsl_distro`
- `platform` — `preferred_shell`, `admin`, `network`, `long_path_support`
- `guidance.use_wrap_for` — ops to prefer `wrap` for, e.g. `["write","copy","move",...]`

```powershell
python scripts/cli.py capabilities
```

`detect` also returns the extended capability block (`python`, `node`, `git`, `cargo`, `docker`, `wsl`, `admin`, `network`, version strings).

## Error recovery & retry loop (P2)

`exec` runs a two-stage pipeline: **Stage 1** naive OS/timeout retries, **Stage 2** a deterministic Error Recovery Engine (no LLM). On failure the engine classifies the error into one of **22 categories**, emits `fix_hint`s, and — where safe — retries once with an auto-recovery action. When several rules match, the first rule that yields an auto-recovery action wins (deterministic).

Auto-recovery categories (retry once):
- `command_not_found` → `where.exe` re-resolves the tool to a full path. If it cannot resolve, **no action is taken** (the failure surfaces honestly — never guesses).
- `pip_not_found` → rewrites bare `pip`/`pip3` to `python -m pip`.
- `execution_policy_blocked` → retries with a **process-scoped** `Set-ExecutionPolicy Bypass` (never changes machine/user policy).
- `encoding_mojibake` → re-runs via `cmd`+GBK codepage.
- `python_not_found` → retries with `python3`.

Suggestion-only categories (no auto-execute): `permission_denied`, `path_not_found`, `syntax_error`, `file_in_use`, `git_not_available`, `node_not_found`, `module_not_found`, `disk_full`, `network_unreachable`, `tls_cert_error`, `auth_failed`, `path_too_long`, `already_exists`, `directory_not_empty`, `argument_error`, `admin_required`, `timeout_or_hung`.

Recovery is on by default; disable with `exec --no-recover`. Analyze a result without executing via `recover`:

```powershell
python scripts/cli.py exec "cat a.txt"           # auto-recovery on
python scripts/cli.py exec "cat a.txt" --no-recover
python scripts/cli.py recover '{"ok":false,"stderr":"...","original":"..."}'
```

## Path resolution (P2)

`resolve` normalizes any path notation into a canonical absolute Windows path so the agent never reasons about separators/prefixes itself: `~`, `.`/`..`, `//server/share` and `\\server\share` UNC, `/mnt/c/...` (WSL mounts), `C:/x//y` (mixed/duplicate separators), and `\\?\` long-path passthrough.

```powershell
python scripts/cli.py resolve "~/Documents"        # -> C:\Users\<you>\Documents
python scripts/cli.py resolve "/mnt/c/Users/me/x"  # -> C:\Users\me\x
```

## Tool discovery (P2)

`discover` resolves a logical tool name to the best concrete executable, preferring real `.exe` over `.cmd`/`.bat`/`.ps1` wrappers (and excluding QClaw's `bash.cmd` false positive). Omit the tool name to probe them all.

```powershell
python scripts/cli.py discover git      # -> resolved path + candidates
python scripts/cli.py discover          # -> all known tools
```

## Common Windows errors

See [references/error-patterns.md](references/error-patterns.md) for common error messages and how this skill handles them.

## Tests

Run the suite from the skill root:

```
python tests/run_all.py
```

It runs two data-driven harnesses:

- `tests/test_recovery_rules.py` over `recovery_fixtures.json` — for every
  recovery rule, asserts (1) positive samples classify to the right category,
  (2) they do **not** trip any undeclared category (cross-rule interference
  net), (3) negative samples stay unmatched, and (4) auto-recovery actions
  resolve as expected.
- `tests/test_adapter_rules.py` over `adapter_fixtures.json` — for every
  command-translation rule, asserts routing (**anti-shadowing net**:
  ordering-sensitive rules like `cat` vs `cat | grep` keep matching the
  intended rule), exact per-shell output, and fallback behavior.

Both include a coverage gate that fails if any registered rule lacks a fixture,
so a new rule cannot land without a test. **Add a fixture entry whenever you
add a recovery rule or a translation rule.**

## Resources

- [references/architecture.md](references/architecture.md) — architecture, call-flow diagrams, and translation engine
- [references/command-registry.md](references/command-registry.md) — command translation rules
- [references/agent-workflow.md](references/agent-workflow.md) — agent usage flow examples
- [references/error-patterns.md](references/error-patterns.md) — error patterns and fixes
- [references/tool-wrappers.md](references/tool-wrappers.md) — safe file/shell operations
- [assets/prompt-template.txt](assets/prompt-template.txt) — system prompt environment template (loaded by `prompt`/`prompt_gen.py`)
- [scripts/cli.py](scripts/cli.py) — main CLI entry

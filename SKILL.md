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
2. Translate a command to PowerShell syntax:
   ```powershell
   python scripts/cli.py translate "rm -rf ./temp"
   ```
3. Execute a translated command:
   ```powershell
   python scripts/cli.py exec "cat a.txt | grep hello"
   ```
4. Generate a system prompt fragment:
   ```powershell
   python scripts/cli.py prompt
   ```
5. Run a safe tool wrapper:
   ```powershell
   python scripts/cli.py wrap mkdir ./build
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

## Common Windows errors

See [references/error-patterns.md](references/error-patterns.md) for common error messages and how this skill handles them.

## Resources

- [references/architecture.md](references/architecture.md) — architecture, call-flow diagrams, and translation engine
- [references/command-registry.md](references/command-registry.md) — command translation rules
- [references/agent-workflow.md](references/agent-workflow.md) — agent usage flow examples
- [references/error-patterns.md](references/error-patterns.md) — error patterns and fixes
- [references/tool-wrappers.md](references/tool-wrappers.md) — safe file/shell operations
- [assets/prompt-template.txt](assets/prompt-template.txt) — system prompt environment template (loaded by `prompt`/`prompt_gen.py`)
- [scripts/cli.py](scripts/cli.py) — main CLI entry
